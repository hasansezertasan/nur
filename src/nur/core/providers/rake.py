from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tree_sitter_ruby
from tree_sitter import Language, Node, Parser

from nur.core.models import Task
from nur.core.providers._rake_syntax import (
    binding_names,
    defined_probe,
    literal,
    node_text,
    syntax_error,
)

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


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _is_self(node: Node | None) -> bool:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        if len(children) != 1:
            return False
        node = children[0]
    return node is not None and node.type == "self"


def _record_override(node: Node, disabled: set[str]) -> None:
    # Direct singleton definitions replace the methods Rake extends main with.
    # A singleton-class body can replace any of them; do not guess its effects.
    if node.type == "singleton_class":
        value = node.child_by_field_name("value")
        if _is_self(value):
            disabled.update({"task", "multitask", "namespace", "desc"})
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if _is_self(owner) and name_node is not None:
            disabled.add(node_text(name_node))


def _method(node: Node, disabled: set[str]) -> str | None:
    _record_override(node, disabled)
    if (
        node.parent is not None
        and node.parent.type in {"begin", "begin_block", "parenthesized_statements"}
        and node.type not in {"begin", "parenthesized_statements"}
    ):
        # Flattened wrappers retain source order while each statement still
        # contributes reachable load-time overrides, including conditionals.
        disabled.update(_dsl_overrides(node, in_scope=True))
    if node.type != "call" or node.child_by_field_name("receiver") is not None:
        return None
    method = node.child_by_field_name("method")
    name = node_text(method) if method is not None else None
    return name if name not in disabled else None


def _argument_array(node: Node) -> bool:
    return node.type == "array" and all(
        child.type == "comment" or literal(child) is not None
        for child in node.named_children
    )


def _order_only_key(node: Node) -> bool:
    return (
        node.type in {"simple_symbol", "hash_key_symbol", "delimited_symbol"}
        and literal(node) == "order_only"
    )


def _valid_task_tail(arguments: list[Node]) -> bool:
    positional = arguments
    pairs: list[Node] | None = None
    if arguments and arguments[-1].type == "hash":
        positional = arguments[:-1]
        pairs = [
            child for child in arguments[-1].named_children if child.type != "comment"
        ]
    else:
        for index, argument in enumerate(arguments):
            if argument.type == "pair":
                positional, pairs = arguments[:index], arguments[index:]
                break
    if pairs is None:
        return (len(positional) == 1 and _argument_array(positional[0])) or all(
            literal(argument) is not None for argument in positional
        )
    if len(pairs) not in {1, 2} or any(pair.type != "pair" for pair in pairs):
        return False
    keys = [pair.child_by_field_name("key") for pair in pairs]
    ordinary = [key for key in keys if key is not None and not _order_only_key(key)]
    if len(ordinary) > 1:
        return False
    key = ordinary[0] if ordinary else None
    # An array dependency key supplies argument names. A nil/absent key uses
    # the first remaining positional argument, or an empty argument list.
    if key is not None and key.type != "nil":
        return _argument_array(key)
    return (
        not positional or positional[0].type == "nil" or _argument_array(positional[0])
    )


def _task_name(arguments: list[Node]) -> str | None:
    if not arguments:
        return None
    first = arguments[0]
    if first.type == "pair":
        # Rake expects one task-name/prerequisites pair, not multiple names.
        if len(arguments) != 1:
            return None
        key = first.child_by_field_name("key")
        if key is None or _order_only_key(key):
            return None
        first = key
    elif not _valid_task_tail(arguments[1:]):
        return None
    name = literal(first)
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
    name = literal(arguments[0])
    block = node.child_by_field_name("block")
    if name is None or not _NAME.fullmatch(name) or block is None:
        return None
    # DSL bindings can change Ruby's parsing of calls inside the namespace.
    parameters = block.child_by_field_name("parameters")
    if parameters is not None and set(binding_names(parameters)) & {
        "task",
        "multitask",
        "namespace",
        "desc",
    }:
        return None
    # Rescue/ensure bodies are conditional, so leave the namespace opaque.
    body = block.child_by_field_name("body")
    if body is None or any(
        child.type in {"rescue", "else", "ensure"} for child in body.named_children
    ):
        return None
    qualified = f"{namespace}:{name}" if namespace else name
    if qualified == "rake" or qualified.startswith("rake:"):
        return None
    return qualified, body


def _make_task(
    descriptions: dict[str, list[str]],
    arguments: list[Node],
    namespace: str,
    description: str | None,
    source_file: str,
) -> Task | None:
    name = _task_name(arguments)
    if name is None:
        return None
    qualified = f"{namespace}:{name}" if namespace else name
    # Rake strips this special lookup prefix instead of invoking that literal name.
    if qualified.startswith("rake:"):
        return None
    comments = descriptions.setdefault(qualified, [])
    if description is not None:
        comment = description.strip()
        if comment and comment not in comments:
            comments.append(comment)
    summary = " / ".join(
        re.split(
            r"(?<=\w)(\.|!)[ \t]|(\.$|!)|\n", comment, flags=re.ASCII | re.MULTILINE
        )[0]
        for comment in comments
    )
    return Task(
        name=qualified,
        prefix="rake",
        argv_base=("rake", qualified),
        description=summary if comments else None,
        source_file=source_file,
    )


def _description(arguments: list[Node]) -> str | None:
    return literal(arguments[0]) if len(arguments) == 1 else None


def _escaping_control(root: Node, *, include_begin: bool = False) -> str | None:
    """Find controls evaluated in this scope, leaving nested bodies opaque."""
    pending = [(root, False)]
    loops = {"while", "until", "for", "while_modifier", "until_modifier"}
    local_control = None
    while pending:
        node, in_loop = pending.pop()
        if defined_probe(node) or (node.type == "begin_block" and not include_begin):
            continue
        if node.type == "return":
            return "return"
        if node.type in {"break", "next", "redo"} and not in_loop:
            if node.type == "redo":
                return "redo"
            local_control = node.type
        if node.type in {"method", "block", "do_block", "lambda", "end_block"}:
            continue
        if node.type == "singleton_method":
            receiver = node.child_by_field_name("object")
            children = [receiver] if receiver is not None else []
        else:
            body = node.child_by_field_name("body")
            children = [
                child
                for child in (
                    _reachable_begin_children(node)
                    if include_begin
                    else node.named_children
                )
                if node.type not in {"class", "module", "singleton_class"}
                or child != body
            ]
        pending.extend((child, in_loop or node.type in loops) for child in children)
    return local_control


def _reachable_begin_children(node: Node) -> list[Node]:
    if defined_probe(node):
        return []
    condition = node.child_by_field_name("condition")
    while condition is not None and condition.type == "parenthesized_statements":
        children = [
            child for child in condition.named_children if child.type != "comment"
        ]
        if len(children) != 1:
            break
        condition = children[0]
    if condition is None or condition.type not in {"true", "false", "nil"}:
        return node.named_children
    truth = condition.type == "true"
    if node.type in {"unless", "unless_modifier", "until", "until_modifier"}:
        truth = not truth
    if node.type in {"if", "unless", "elsif", "conditional"}:
        branch = node.child_by_field_name("consequence" if truth else "alternative")
        return [branch] if branch is not None else []
    if not truth and node.type in {
        "if_modifier",
        "unless_modifier",
        "while",
        "until",
        "while_modifier",
        "until_modifier",
    }:
        body = node.child_by_field_name("body")
        # BEGIN and begin/end loop modifiers execute their body once first.
        if (
            node.type in {"while_modifier", "until_modifier"}
            and body is not None
            and body.type in {"begin_block", "begin"}
        ):
            return node.named_children
        return []
    return node.named_children


def _dsl_overrides(root: Node, *, in_scope: bool = False) -> set[str]:
    # Ruby executes BEGIN bodies before ordinary statements, even when the
    # BEGIN appears later in the file or has an active postfix condition.
    disabled: set[str] = set()
    pending = [(root, in_scope)]
    opaque = {
        "method",
        "singleton_method",
        "class",
        "module",
        "singleton_class",
        "block",
        "do_block",
        "lambda",
        "end_block",
    }
    while pending:
        node, in_begin = pending.pop()
        in_begin = in_begin or node.type == "begin_block"
        if in_begin:
            _record_override(node, disabled)
        if node.type not in opaque:
            pending.extend(
                (child, in_begin) for child in _reachable_begin_children(node)
            )
    return disabled


def _begin_exits(root: Node) -> bool:
    pending = [root]
    modifiers = {
        "if_modifier",
        "unless_modifier",
        "while_modifier",
        "until_modifier",
        "rescue_modifier",
    }
    while pending:
        node = pending.pop()
        if node.type == "begin_block":
            initializer = node
            while (
                initializer.parent is not None and initializer.parent.type in modifiers
            ):
                initializer = initializer.parent
            if _escaping_control(initializer, include_begin=True) == "return":
                return True
        pending.extend(_reachable_begin_children(node))
    return False


def _scope_statements(root: Node) -> Iterator[Node]:
    # A rescue-free begin/end wrapper shares the surrounding lexical scope,
    # pending description, and execution order. Keep exception handlers opaque.
    pending = [iter(root.named_children)]
    while pending:
        node = next(pending[-1], None)
        if node is None:
            pending.pop()
        elif node.type == "begin_block" or (
            (body := node.child_by_field_name("body")) is not None
            and body.type == "begin_block"
        ):
            continue
        elif node.type == "parenthesized_statements" or (
            node.type == "begin"
            and not any(
                child.type in {"rescue", "else", "ensure"}
                for child in node.named_children
            )
        ):
            pending.append(iter(node.named_children))
        else:
            yield node


def _begin_statements(root: Node, disabled: set[str]) -> Iterator[Node]:
    # MRI hoists each nested initializer before its enclosing BEGIN body.
    pending = [(root, False)]
    while pending:
        node, completed = pending.pop()
        if completed:
            yield from _scope_statements(node)
        elif node.type in {"program", "begin_block"}:
            if node.type == "begin_block":
                pending.append((node, True))
            pending.extend((child, False) for child in reversed(node.named_children))
        elif node.type in {"if_modifier", "unless_modifier"}:
            condition = node.child_by_field_name("condition")
            while (
                condition is not None and condition.type == "parenthesized_statements"
            ):
                children = [
                    child
                    for child in condition.named_children
                    if child.type != "comment"
                ]
                condition = children[0] if len(children) == 1 else None
            if condition is not None and condition.type in {"true", "false", "nil"}:
                pending.extend(
                    (child, False)
                    for child in reversed(_reachable_begin_children(node))
                )
            else:
                disabled.update(_dsl_overrides(node))
        elif node.type in {"while_modifier", "until_modifier", "rescue_modifier"}:
            body = node.child_by_field_name("body")
            if body is not None and body.type == "begin_block":
                pending.append((body, False))


def _load_statements(root: Node, disabled: set[str]) -> Iterator[Node]:
    yield from _begin_statements(root, disabled)
    # Unknown initializer conditions remain opaque, but their possible overrides
    # still prevent ordinary calls from being mistaken for the Rake DSL.
    disabled.update(_dsl_overrides(root))
    yield from _scope_statements(root)


def _in_initializer(node: Node) -> bool:
    parent = node.parent
    while parent is not None and parent.type not in {"begin_block", "program"}:
        parent = parent.parent
    return parent is not None and parent.type == "begin_block"


def _declarations(root: Node) -> Iterator[tuple[Node, str, str | None]]:
    # Each frame owns its pending description; it cannot leak out of a scope.
    # An explicit stack avoids Python recursion on deeply nested namespaces.
    disabled: set[str] = set()
    scopes: list[tuple[Iterator[Node], str, str | None]] = [
        (_load_statements(root, disabled), "", None)
    ]
    while scopes:
        statements, namespace, description = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        control = _escaping_control(node, include_begin=_in_initializer(node))
        if control in {"return", "redo"}:
            return
        if control in {"break", "next"}:
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
                scopes.append((_scope_statements(body), qualified, None))


def parse_rakefile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Discover literal Rake tasks at file scope or inside literal namespaces.

    Only direct ``task``/``multitask`` calls and adjacent literal ``desc`` calls
    are read. Task bodies, conditional/generated declarations, file tasks,
    rules and imported files are opaque. Parentheses and plain begin/end
    wrappers without exception handlers are transparent. Active literal BEGIN
    initializers are read before ordinary statements. No Ruby code or
    runner is executed. Regexp literals outside the validated ASCII syntax
    subset cause the file to be skipped.
    """
    root = Parser(_LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        log.warning("nur: skipping %s (invalid Ruby syntax)", source_file)
        return []
    control_error = syntax_error(root)
    if control_error is not None:
        log.warning("nur: skipping %s (%s)", source_file, control_error)
        return []
    if _begin_exits(root):
        return []
    tasks: dict[str, Task] = {}
    descriptions: dict[str, list[str]] = {}
    for node, namespace, description in _declarations(root):
        task = _make_task(
            descriptions, _arguments(node), namespace, description, source_file
        )
        if task is not None:
            tasks[task.name] = task
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
