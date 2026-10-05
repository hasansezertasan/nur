from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tree_sitter_ruby
from tree_sitter import Language, Node, Parser

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["RakeProvider", "parse_rakefile"]


log = logging.getLogger("nur")
_SOURCE_FILE = "Rakefile"
_LANGUAGE = Language(tree_sitter_ruby.language())
# Rake interprets leading '-' as an option, '=' as an environment assignment,
# and brackets as task arguments. Accept a conservative runnable-name subset.
_NAME = re.compile(r"[\w][\w:./-]*\Z")


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def _literal(node: Node) -> str | None:
    """Read a Ruby symbol or ordinary quoted string without evaluating it.

    Escapes, interpolation, heredocs and percent literals are deliberately
    omitted: decoding them would require additional Ruby semantics.
    """
    if node.type == "simple_symbol":
        return _text(node)[1:]
    if node.type == "hash_key_symbol":
        return _text(node)
    if node.type not in {"string", "delimited_symbol"}:
        return None
    raw = _text(node)
    if node.type == "delimited_symbol":
        raw = raw[1:]
    if not raw.startswith(("'", '"')):
        return None
    if any(child.type != "string_content" for child in node.named_children):
        return None
    return raw[1:-1]


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _method(node: Node) -> str | None:
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return None
    method = node.child_by_field_name("method")
    return _text(method) if method is not None else None


def _task_name(arguments: list[Node]) -> str | None:
    if not arguments:
        return None
    first = arguments[0]
    if first.type == "pair":
        # Rake expects one task-name/prerequisites pair, not multiple names.
        if len(arguments) != 1:
            return None
        key = first.child_by_field_name("key")
        if key is None:
            return None
        first = key
    name = _literal(first)
    # Rake strips this special lookup prefix instead of invoking that literal name.
    return (
        name
        if name is not None and _NAME.fullmatch(name) and not name.startswith("rake:")
        else None
    )


def _namespace_body(node: Node, arguments: list[Node]) -> tuple[str, Node] | None:
    if len(arguments) != 1:
        return None
    name = _literal(arguments[0])
    block = node.child_by_field_name("block")
    if name is None or not _NAME.fullmatch(name) or block is None:
        return None
    # Parameters may shadow the Rake DSL. Rescue/ensure bodies are conditional;
    # skip the whole namespace rather than claiming its tasks are unconditional.
    if block.child_by_field_name("parameters") is not None:
        return None
    body = block.child_by_field_name("body")
    if body is None or any(
        child.type in {"rescue", "else", "ensure"} for child in body.named_children
    ):
        return None
    return name, body


def _add_task(
    tasks: dict[str, Task],
    arguments: list[Node],
    namespace: str,
    description: str | None,
    source_file: str,
) -> None:
    name = _task_name(arguments)
    if name is None:
        return
    qualified = f"{namespace}:{name}" if namespace else name
    previous = tasks.get(qualified)
    tasks[qualified] = Task(
        name=qualified,
        prefix="rake",
        argv_base=("rake", qualified),
        description=description or (previous.description if previous else None),
        source_file=source_file,
    )


def parse_rakefile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Discover literal Rake tasks at file scope or inside literal namespaces.

    Only direct ``task``/``multitask`` calls and adjacent literal ``desc`` calls
    are read. Task bodies, conditional/generated declarations, file tasks,
    rules and imported files are opaque. No Ruby code or runner is executed.
    """
    root = Parser(_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        log.warning("nur: skipping %s (invalid Ruby syntax)", source_file)
        return []

    # Each frame owns its pending description; it cannot leak out of a scope.
    # An explicit stack avoids Python recursion on deeply nested namespaces.
    scopes: list[tuple[Iterator[Node], str, str | None]] = [
        (iter(root.named_children), "", None)
    ]
    tasks: dict[str, Task] = {}
    while scopes:
        statements, namespace, description = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        method = _method(node)
        arguments = _arguments(node)
        nested: tuple[str, Node] | None = None
        if node.type == "comment":
            pass
        elif method == "desc":
            description = _literal(arguments[0]) if len(arguments) == 1 else None
        else:
            if method in {"task", "multitask"}:
                _add_task(tasks, arguments, namespace, description, source_file)
            elif method == "namespace":
                nested = _namespace_body(node, arguments)
            description = None
        scopes.append((statements, namespace, description))
        if nested is not None:
            name, body = nested
            qualified = f"{namespace}:{name}" if namespace else name
            if not (qualified == "rake" or qualified.startswith("rake:")):
                scopes.append((iter(body.named_children), qualified, None))
    return list(tasks.values())


class RakeProvider:
    prefix = "rake"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        return parse_rakefile(text)
