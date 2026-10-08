from __future__ import annotations

import codecs
import logging
import re
from typing import TYPE_CHECKING, TypeAlias

import tree_sitter_make
from tree_sitter import Language, Parser

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tree_sitter import Node

__all__ = ["MakeProvider"]


log = logging.getLogger("nur")

_SOURCE_FILE = "Makefile"
_PARSER = Parser(Language(tree_sitter_make.language()))
# A rule's left-hand side for the line-based fallback: one or more target names
# before ':' or '::'. The negative lookahead excludes ':=' assignments; recipe
# lines are tab-indented so they never start with [A-Za-z0-9].
_RULE_RE = re.compile(r"^([A-Za-z0-9][^:=#]*?)\s*::?(?!=)")
# A single valid target name (excludes '%' pattern rules and '.'-prefixed).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
# An inline "## description".
_DESC_RE = re.compile(r"##\s*(.*?)\s*$")
# Subtrees that never contain rule headers: define bodies are raw text and
# recipe lines are shell commands.
_OPAQUE = {"define_directive", "recipe_line", "include_directive"}
# A newline that is not escaped by a trailing backslash.
_BARE_NEWLINE = re.compile(rb"(?<!\\)(?<!\\\r)\n")
# Bytes that may directly surround a target name ('' is start/end of input).
_BOUNDARY = {b"", b" ", b"\t", b"\r", b"\n", b"\f", b":", b"\\"}

_Found: TypeAlias = tuple[str, str | None]  # noqa: UP040 -- AST hooks run Python 3.10.


def _line_targets(line: str) -> list[_Found]:
    """Scan one line for rule targets (fallback for text the grammar rejects)."""
    match = _RULE_RE.match(line)
    if not match:
        return []
    desc_match = _DESC_RE.search(line)
    desc = desc_match.group(1) if desc_match else None
    return [
        (token, desc)
        for token in match.group(1).split()
        if _NAME_RE.match(token) and token != _SOURCE_FILE
    ]


def _fallback(source: bytes, node: Node) -> list[_Found]:
    """Line-scan the full lines spanned by *node*."""
    start = source.rfind(b"\n", 0, node.start_byte) + 1
    end = source.find(b"\n", max(start, node.end_byte - 1))
    if end == -1:
        end = len(source)
    text = source[start:end].decode("utf-8", errors="replace")
    text = text.replace("\\\n", " ")
    depth = 0
    found: list[_Found] = []
    for line in text.splitlines():
        if re.match(r"^ *(?:(?:override|export)[ \t]+)*define(?:[ \t]|$)", line):
            depth += 1
        elif re.match(r"^ *endef(?:[ \t]|$)", line):
            depth = max(0, depth - 1)
        elif depth == 0:
            found.extend(_line_targets(line))
    return found


def _delimited(source: bytes, word: Node) -> bool:
    """Return whether *word* is a whole token, not the tail of an unparsed one.

    The grammar splits names at characters it rejects (``über`` becomes an
    error plus the word ``ber``), so a word glued to other text is a fragment.
    """
    before = source[word.start_byte - 1 : word.start_byte]
    after = source[word.end_byte : word.end_byte + 1]
    return before in _BOUNDARY and after in _BOUNDARY


def _is_clean(source: bytes, rule: Node, targets: Node | None) -> bool:
    """Return whether the grammar parsed *rule*'s header faithfully."""
    if targets is None or targets.has_error:
        return False
    if source[rule.start_byte - 1 : rule.start_byte] not in {b"", b"\n"}:
        return False
    return _BARE_NEWLINE.search(source, targets.start_byte, targets.end_byte) is None


def _rule_targets(source: bytes, rule: Node, targets: Node) -> list[_Found]:
    """Read literal targets and the same-line ``##`` description of a clean rule."""
    desc: str | None = None
    header_end = targets.end_byte
    for child in rule.named_children:
        if child.type == "comment":
            gap = source[header_end : child.start_byte]
            match = _DESC_RE.search((child.text or b"").decode("utf-8", "replace"))
            if match and b"\n" not in gap:
                desc = match.group(1)
            break
        if child.type != "recipe":
            header_end = child.end_byte
    found: list[_Found] = []
    for child in targets.named_children:
        name = (child.text or b"").decode("utf-8", errors="replace")
        if (
            child.type == "word"
            and _NAME_RE.match(name)
            and name != _SOURCE_FILE
            and _delimited(source, child)
        ):
            found.append((name, desc))
    return found


def _walk(source: bytes, root: Node) -> Iterator[_Found]:
    """Yield targets in source order.

    Clean rules come from tree-sitter nodes. Anything the grammar rejects or
    fuses with its neighbours (error nodes, target-specific variable lines,
    headers glued to preceding text) is re-read line by line, so one construct
    the grammar cannot parse does not hide the rules around it.

    Yields:
        ``(name, description)`` pairs.
    """
    stack = list(reversed(root.children))
    while stack:
        node = stack.pop()
        if node.type in _OPAQUE:
            continue
        if node.type in {"variable_assignment", "ERROR"}:
            # `a b: FLAGS=-x` is a target-specific variable but still declares
            # the targets; `X := 1` yields nothing from the line scan.
            yield from _fallback(source, node)
            continue
        if node.type == "rule":
            targets = next(
                (c for c in node.named_children if c.type == "targets"), None
            )
            if targets is None or not _is_clean(source, node, targets):
                yield from _fallback(source, node)
                continue
            yield from _rule_targets(source, node, targets)
        stack.extend(reversed(node.children))


def _scan(source: bytes) -> list[_Found]:
    """Return ``(name, description)`` per literal target, in source order."""
    source = source.removeprefix(codecs.BOM_UTF8)
    source = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not source.endswith(b"\n"):
        source += b"\n"
    tree = _PARSER.parse(source)
    if tree.root_node.has_error:
        log.warning(
            "nur: %s has syntax the parser rejects; reading those parts line by line",
            _SOURCE_FILE,
        )
    return list(_walk(source, tree.root_node))


def _descriptions(found: list[_Found]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, desc in found:
        if desc:
            out.setdefault(name, desc)
    return out


class MakeProvider:
    prefix = "make"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            source = (cwd / _SOURCE_FILE).read_bytes()
        except OSError as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        found = _scan(source)
        descriptions = _descriptions(found)
        return [
            Task(
                name=name,
                prefix=self.prefix,
                argv_base=("make", name),
                description=descriptions.get(name),
                source_file=_SOURCE_FILE,
            )
            for name in dict.fromkeys(name for name, _ in found)
        ]
