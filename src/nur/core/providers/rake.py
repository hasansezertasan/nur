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


def _method(node: Node, disabled: set[str]) -> str | None:
    # Direct singleton definitions replace the methods Rake extends main with.
    # A singleton-class body can replace any of them; do not guess its effects.
    if node.type == "singleton_class":
        value = node.child_by_field_name("value")
        if value is not None and value.type == "self":
            disabled.update({"task", "multitask", "namespace", "desc"})
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if owner is not None and owner.type == "self" and name_node is not None:
            disabled.add(_text(name_node))
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return None
    method = node.child_by_field_name("method")
    name = _text(method) if method is not None else None
    return name if name not in disabled else None


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
    # Rake strips trailing colons from string task names; omit this ambiguous form.
    return (
        name
        if name is not None and _NAME.fullmatch(name) and not name.endswith(":")
        else None
    )


def _namespace_body(
    node: Node, arguments: list[Node], namespace: str
) -> tuple[str, Node] | None:
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
    qualified = f"{namespace}:{name}" if namespace else name
    if qualified == "rake" or qualified.startswith("rake:"):
        return None
    return qualified, body


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
    # Rake strips this special lookup prefix instead of invoking that literal name.
    if qualified.startswith("rake:"):
        return
    previous = tasks.get(qualified)
    tasks[qualified] = Task(
        name=qualified,
        prefix="rake",
        argv_base=("rake", qualified),
        description=description or (previous.description if previous else None),
        source_file=source_file,
    )


def _description(arguments: list[Node]) -> str | None:
    return _literal(arguments[0]) if len(arguments) == 1 else None


def _control_flow_error(root: Node) -> str | None:
    """Validate Ruby control placement beyond the grammar's syntax shapes.

    Invalid placement prevents the whole file loading, even in opaque task
    bodies. Retry needs a rescue; break/next/redo need a block or loop. New
    method/class scopes reset both permissions; ensure resets retry only.
    """
    pending = [(root, False, False)]
    new_scopes = {"method", "singleton_method", "class", "singleton_class", "module"}
    blocks = {"lambda", "block", "do_block"}
    loops = {"while", "until", "for", "while_modifier", "until_modifier"}
    while pending:
        node, in_rescue, in_iteration = pending.pop()
        if node.type == "retry" and not in_rescue:
            return "retry outside rescue"
        if node.type in {"break", "next", "redo"} and not in_iteration:
            return f"{node.type} outside block or loop"
        if node.type in new_scopes:
            in_rescue = in_iteration = False
        if node.type in blocks or node.type == "ensure":
            in_rescue = False
        body = node.child_by_field_name("body")
        rescue_body = body if node.type == "rescue" else None
        iteration_body = body if node.type in blocks | loops else None
        pending.extend(
            (
                child,
                in_rescue or child == rescue_body,
                in_iteration or child == iteration_body,
            )
            for child in node.named_children
        )
    return None


def _declarations(root: Node) -> Iterator[tuple[Node, str, str | None]]:
    # Each frame owns its pending description; it cannot leak out of a scope.
    # An explicit stack avoids Python recursion on deeply nested namespaces.
    scopes: list[tuple[Iterator[Node], str, str | None]] = [
        (iter(root.named_children), "", None)
    ]
    disabled: set[str] = set()
    while scopes:
        statements, namespace, description = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        if node.type in {"return", "redo"}:
            return
        if node.type in {"break", "next"}:
            continue
        method = _method(node, disabled)
        arguments = _arguments(node)
        if node.type == "comment":
            scopes.append((statements, namespace, description))
            continue
        if method == "desc":
            scopes.append((statements, namespace, _description(arguments)))
            continue
        scopes.append((statements, namespace, None))
        if method in {"task", "multitask"}:
            yield node, namespace, description
        elif method == "namespace":
            nested = _namespace_body(node, arguments, namespace)
            if nested is not None:
                qualified, body = nested
                scopes.append((iter(body.named_children), qualified, None))


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
    control_error = _control_flow_error(root)
    if control_error is not None:
        log.warning("nur: skipping %s (%s)", source_file, control_error)
        return []
    tasks: dict[str, Task] = {}
    for node, namespace, description in _declarations(root):
        _add_task(tasks, _arguments(node), namespace, description, source_file)
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
