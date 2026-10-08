from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import tree_sitter_ruby
from tree_sitter import Language, Parser

from nur.core.models import Task
from nur.core.providers._rake_source import decode_source

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tree_sitter import Node

__all__ = ["RakeProvider", "parse_rakefile"]

log = logging.getLogger("nur")
_SOURCE_FILE = "Rakefile"
_LANGUAGE = Language(tree_sitter_ruby.language())
_NAME = re.compile(r"[\w][\w:./-]*\Z")
_HANDLERS = {"rescue", "else", "ensure"}


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def _unwrapped(node: Node | None) -> Node | None:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    return node


def _literal(node: Node | None) -> str | None:
    """Read the supported unescaped literal subset without Ruby evaluation."""
    node = _unwrapped(node)
    if node is None:
        return None
    if node.type == "simple_symbol":
        return _node_text(node)[1:]
    if node.type == "hash_key_symbol":
        return _node_text(node)
    if node.type not in {"string", "delimited_symbol"}:
        return None
    raw = _node_text(node)
    if node.type == "delimited_symbol":
        raw = raw[1:]
    if not raw.startswith(("'", '"')) or any(
        child.type != "string_content" for child in node.named_children
    ):
        return None
    return raw[1:-1]


def _name(node: Node | None) -> str | None:
    value = _literal(node)
    return (
        value
        if value is not None and _NAME.fullmatch(value) and not value.endswith(":")
        else None
    )


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    return (
        [
            child
            for child in arguments.named_children
            if child.type not in {"comment", "block_argument"}
        ]
        if arguments is not None
        else []
    )


def _method(node: Node) -> str | None:
    receiver = node.child_by_field_name("receiver")
    target = _unwrapped(receiver)
    unsupported_receiver = receiver is not None and (
        target is None or target.type != "self"
    )
    if node.type != "call" or unsupported_receiver:
        return None
    method = node.child_by_field_name("method")
    return _node_text(method) if method is not None else None


def _task_name(arguments: list[Node]) -> str | None:
    if not arguments:
        return None
    first = _unwrapped(arguments[0])
    if first is None:
        return None
    if first.type in {"pair", "hash"}:
        pairs = (
            [child for child in first.named_children if child.type != "comment"]
            if first.type == "hash"
            else arguments
        )
        if len(pairs) != 1 or pairs[0].type != "pair":
            return None
        return _name(pairs[0].child_by_field_name("key"))
    return _name(first)


def _description(arguments: list[Node]) -> str | None:
    node = _unwrapped(arguments[0]) if len(arguments) == 1 else None
    value = _literal(node) if node is not None and node.type == "string" else None
    return value.strip().splitlines()[0] if value and value.strip() else None


def _statements(root: Node) -> Iterator[Node]:
    pending = [iter(root.named_children)]
    while pending:
        node = next(pending[-1], None)
        if node is None:
            pending.pop()
            continue
        children = node.named_children
        transparent = node.type == "parenthesized_statements" or (
            node.type == "begin"
            and not any(child.type in _HANDLERS for child in children)
        )
        if transparent and any(child.type != "comment" for child in children):
            pending.append(iter(children))
        else:
            yield node


def _namespace(node: Node, parent: str) -> tuple[str, Node] | None:
    arguments = _arguments(node)
    name = _name(arguments[0]) if len(arguments) == 1 else None
    block = node.child_by_field_name("block")
    if name is None or block is None:
        return None
    parameters = block.child_by_field_name("parameters")
    if parameters is not None and parameters.named_children:
        return None
    body = block.child_by_field_name("body")
    if body is None or any(child.type in _HANDLERS for child in body.named_children):
        return None
    qualified = f"{parent}:{name}" if parent else name
    return (
        (qualified, body)
        if qualified != "rake" and not qualified.startswith("rake:")
        else None
    )


def _make_task(
    node: Node, namespace: str, description: str | None, source_file: str
) -> Task | None:
    name = _task_name(_arguments(node))
    if name is None:
        return None
    qualified = f"{namespace}:{name}" if namespace else name
    if qualified.startswith("rake:"):
        return None
    return Task(
        name=qualified,
        prefix="rake",
        argv_base=("rake", qualified),
        description=description,
        source_file=source_file,
    )


def _declarations(root: Node, source_file: str) -> Iterator[Task]:
    scopes: list[tuple[Iterator[Node], str, str | None]] = [
        (_statements(root), "", None)
    ]
    while scopes:
        statements, namespace, description = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        method = _method(node)
        if method == "desc":
            description = _description(_arguments(node))
        elif method in {"task", "multitask"}:
            task = _make_task(node, namespace, description, source_file)
            if task is not None:
                yield task
            description = None
        elif method == "namespace":
            scopes.append((statements, namespace, None))
            nested = _namespace(node, namespace)
            if nested is not None:
                qualified, body = nested
                scopes.append((_statements(body), qualified, None))
            continue
        elif node.type != "comment":
            description = None
        scopes.append((statements, namespace, description))


def parse_rakefile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract literal task candidates without evaluating Ruby behavior.

    Direct task/multitask declarations and literal namespaces are supported.
    Parentheses and handler-free begin wrappers are transparent. Other scopes
    are opaque. Parser errors skip the file; Ruby semantic validity, execution
    order, method overrides and successful task registration are not inferred.
    """
    root = Parser(_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        log.warning("nur: skipping %s (unparsable Ruby syntax)", source_file)
        return []
    tasks: dict[str, Task] = {}
    for candidate in _declarations(root, source_file):
        task = candidate
        existing = tasks.get(task.name)
        if existing is not None and task.description is None:
            task = replace(task, description=existing.description)
        tasks[task.name] = task
    return list(tasks.values())


class RakeProvider:
    prefix = "rake"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text, encoding = decode_source((cwd / _SOURCE_FILE).read_bytes())
        except (OSError, UnicodeError, LookupError, ValueError) as error:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, error)
            return []
        tasks = parse_rakefile(text)
        if encoding != "utf-8":
            for task in tasks:
                if not task.name.isascii():
                    log.warning(
                        "nur: skipping %s "
                        "(task name cannot be reproduced from %s source)",
                        task.name,
                        encoding,
                    )
            tasks = [task for task in tasks if task.name.isascii()]
        return tasks
