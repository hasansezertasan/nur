from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tree_sitter_make
from tree_sitter import Language, Parser

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tree_sitter import Node

__all__ = ["MakeProvider", "parse_descriptions", "parse_targets"]


log = logging.getLogger("nur")

_SOURCE_FILE = "Makefile"
_PARSER = Parser(Language(tree_sitter_make.language()))
# A single valid target name (excludes '%' pattern rules and '.'-prefixed).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
# An inline "## description" anywhere on the header line.
_DESC_RE = re.compile(r"##\s*(.*?)\s*$")
# Subtrees that never contain rule headers: define bodies are raw text and
# recipe lines are shell commands.
_OPAQUE = {"define_directive", "recipe_line", "ERROR"}
# Real Makefiles nest conditionals a few levels; deeper input is hostile.
_MAX_DEPTH = 100
# Bytes that may directly surround a target name ('' is start/end of input).
_BOUNDARY = {b"", b" ", b"\t", b"\r", b"\n", b":", b"\\"}


def _rules(root: Node) -> Iterator[Node]:
    """Walk *root* for rules, skipping opaque and erroneous subtrees.

    The walk is iterative and depth-bounded: tree-sitter's node accessors
    crash the interpreter on pathologically nested trees (hundreds of levels).

    Yields:
        Rule nodes in source order.
    """
    stack = [(child, 1) for child in reversed(root.children)]
    while stack:
        node, depth = stack.pop()
        if node.type in _OPAQUE:
            continue
        if depth > _MAX_DEPTH:
            log.warning("nur: %s nesting too deep; skipping the rest", _SOURCE_FILE)
            continue
        if node.type == "rule":
            yield node
        stack.extend((child, depth + 1) for child in reversed(node.children))


def _delimited(source: bytes, word: Node) -> bool:
    """Return whether *word* is a whole token, not the tail of an unparsed one.

    The grammar splits names at characters it rejects (``über`` becomes an
    error plus the word ``ber``), so a word glued to other text is a fragment.
    """
    before = source[word.start_byte - 1 : word.start_byte]
    after = source[word.end_byte : word.end_byte + 1]
    return before in _BOUNDARY and after in _BOUNDARY


def _header_line(rule: Node, lines: list[bytes]) -> str:
    """Return the source line where the rule header (before any recipe) ends."""
    row = max(
        (
            child.end_point.row
            for child in rule.named_children
            if child.type != "recipe"
        ),
        default=rule.start_point.row,
    )
    return lines[row].decode("utf-8", errors="replace")


def _scan(source: bytes) -> list[tuple[str, str | None]]:
    """Return ``(name, description)`` per literal target, in rule order."""
    if not source.endswith(b"\n"):
        source += b"\n"
    tree = _PARSER.parse(source)
    if tree.root_node.has_error:
        log.warning(
            "nur: %s has syntax errors; unparsable parts are skipped", _SOURCE_FILE
        )
    lines = source.split(b"\n")
    found: list[tuple[str, str | None]] = []
    for rule in _rules(tree.root_node):
        targets = next((c for c in rule.named_children if c.type == "targets"), None)
        if targets is None or targets.has_error:
            continue
        match = _DESC_RE.search(_header_line(rule, lines))
        desc = match.group(1) if match else None
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


def _descriptions(found: list[tuple[str, str | None]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, desc in found:
        if desc:
            out.setdefault(name, desc)
    return out


def parse_targets(text: str) -> list[str]:
    """Extract literal target names from Makefile *text* without executing anything.

    Deliberately does NOT shell out to ``make``: the database dump
    (``make -pRrq``) still evaluates ``$(shell ...)`` / ``!=`` assignments while
    reading the file, so a repository's Makefile could run arbitrary commands
    merely by discovering/listing tasks. Tree-sitter reads rule headers
    structurally, at the cost of not resolving ``include`` directives, variable
    expansion or computed targets. Names inside ``define`` blocks, recipes and
    assignments are never reported; unparsable regions are skipped.
    """
    return list(dict.fromkeys(name for name, _ in _scan(text.encode())))


def parse_descriptions(text: str) -> dict[str, str]:
    return _descriptions(_scan(text.encode()))


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
