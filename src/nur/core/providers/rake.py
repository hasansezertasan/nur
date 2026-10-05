from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import tree_sitter_ruby
from tree_sitter import Language, Node, Parser

from nur.core.models import Task
from nur.core.providers._rake_callbacks import (
    constructor_arguments_error,
    invalid_namespace_lambda_parameters,
)
from nur.core.providers._rake_overrides import (
    main_scope,
    record_override,
    scope_headers,
)
from nur.core.providers._rake_syntax import (
    binding_names,
    defined_probe,
    is_self,
    literal,
    node_text,
    syntax_error,
    unbound_identifier_ids,
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
_DEFERRED_METHODS = {
    "task",
    "multitask",
    "file",
    "file_create",
    "directory",
    "rule",
    "desc",
    "proc",
    "lambda",
    "define_method",
    "define_singleton_method",
}

_RAKE_METHODS = _DEFERRED_METHODS - {
    "proc",
    "lambda",
    "define_method",
    "define_singleton_method",
} | {"namespace"}


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [
        child
        for child in arguments.named_children
        if child.type not in {"comment", "block_argument"}
    ]


def _block_arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    return (
        [child for child in arguments.named_children if child.type == "block_argument"]
        if arguments is not None
        else []
    )


def _dsl_receiver(node: Node) -> bool:
    receiver = node.child_by_field_name("receiver")
    return receiver is None or is_self(receiver)


def _method(node: Node, disabled: set[str]) -> str | None:
    # Every declaration-scope statement contributes reachable load-time
    # overrides, including top-level and namespace conditionals.
    disabled.update(_dsl_overrides(node, in_scope=True))
    if node.type == "undef" and any(
        (literal(child) or node_text(child)) in _RAKE_METHODS
        for child in node.named_children
    ):
        return "undef"
    if node.type != "call" or not _dsl_receiver(node):
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


def _task_arguments(arguments: list[Node]) -> tuple[list[Node], list[Node] | None]:
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
    return positional, pairs


def _valid_task_tail(arguments: list[Node]) -> bool:
    positional, pairs = _task_arguments(arguments)
    if pairs is None:
        return (len(positional) == 1 and _argument_array(positional[0])) or all(
            literal(argument) is not None for argument in positional
        )
    keys = _known_hash_keys(pairs)
    if keys is None:
        return False
    ordinary = [key for key in keys if not _order_only_key(key)]
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


class _InvalidDeclarationError(ValueError):
    """A direct Rake declaration is known to fail while loading."""


def _literal_kind(node: Node | None) -> str | None:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    if node is None:
        return None
    if node.type == "unary":
        operator = node.child_by_field_name("operator")
        operand = node.child_by_field_name("operand")
        if (
            operator is not None
            and operator.type in {"+", "-"}
            and operand is not None
            and operand.type in {"integer", "float"}
        ):
            return operand.type
    return "hash" if node.type == "pair" else node.type


def _invalid_block_arguments(node: Node) -> bool:
    if node.type != "call":
        return False
    for argument in _block_arguments(node):
        value = next(
            (child for child in argument.named_children if child.type != "comment"),
            None,
        )
        if _literal_kind(value) in {
            "integer",
            "float",
            "true",
            "false",
            "string",
            "array",
            "regex",
            "range",
        }:
            return True
    return False


def _call_arity(arguments: list[Node]) -> int | None:
    if any(
        argument.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for argument in arguments
    ):
        return None
    positional, pairs = _task_arguments(arguments)
    return len(positional) + int(pairs is not None)


def _invalid_argument_name(node: Node) -> bool:
    return _literal_kind(node) in {
        "lambda",
        "nil",
        "true",
        "false",
        "integer",
        "float",
        "array",
        "hash",
        "regex",
        "range",
    }


def _invalid_argument_names(arguments: list[Node]) -> bool:
    if len(arguments) == 1 and arguments[0].type == "array":
        arguments = arguments[0].named_children
    return any(_invalid_argument_name(argument) for argument in arguments)


def _key_identity(root: Node) -> str | None:
    pending = [(root, False)]
    identity: list[str] = []
    while pending:
        node, closing = pending.pop()
        if closing:
            identity.append("]")
        elif node.type == "array":
            identity.append("[")
            pending.append((node, True))
            pending.extend(
                (child, False)
                for child in reversed(node.named_children)
                if child.type != "comment"
            )
        elif node.type == "nil":
            identity.append("nil")
        else:
            value = literal(node)
            if value is None:
                return None
            kind = "string" if node.type == "string" else "symbol"
            identity.append(repr((kind, value)))
    return repr(identity)


def _known_hash_keys(pairs: list[Node]) -> list[Node] | None:
    if any(pair.type != "pair" for pair in pairs):
        return None
    keys: dict[str, Node] = {}
    for pair in pairs:
        key = pair.child_by_field_name("key")
        identity = _key_identity(key) if key is not None else None
        if key is None or identity is None:
            return None
        keys[identity] = key
    return list(keys.values())


def _invalid_argument_names_value(node: Node | None) -> bool:
    if node is None or node.type == "nil":
        return False
    if node.type == "array":
        return any(_invalid_argument_name(child) for child in node.named_children)
    return _invalid_argument_name(node) or literal(node) is not None


def _invalid_task_arguments(arguments: list[Node]) -> bool:
    positional, pairs = _task_arguments(arguments)
    if pairs is None:
        return _invalid_argument_names(positional[1:])
    keys = _known_hash_keys(pairs)
    if keys is None:
        return False
    ordinary = [key for key in keys if not _order_only_key(key)]
    if len(keys) not in {1, 2} or len(ordinary) > 1:
        return True
    if not positional:
        return False
    key = ordinary[0] if ordinary else None
    if key is None or key.type == "nil":
        key = positional[1] if len(positional) > 1 else None
    return _invalid_argument_names_value(key)


def _namespace_lambda_error(callback: Node, disabled: set[str]) -> bool:
    while (
        callback.type == "parenthesized_statements"
        and len(callback.named_children) == 1
    ):
        callback = callback.named_children[0]
    if callback.type != "lambda":
        method = callback.child_by_field_name("method")
        if callback.type != "call" or method is None or node_text(method) != "lambda":
            return False
        if not _deferred_call(callback, method, disabled):
            return False
        block = callback.child_by_field_name("block")
        if block is None:
            return False
        callback = block
    return invalid_namespace_lambda_parameters(callback)


def _invalid_namespace(node: Node, arguments: list[Node], disabled: set[str]) -> bool:
    callbacks = _block_arguments(node)
    if any(
        _namespace_lambda_error(child, disabled)
        for callback in callbacks
        for child in callback.named_children
    ):
        return True
    if node.child_by_field_name("block") is None and (
        not callbacks
        or any(
            any(
                _literal_kind(child) in {"nil", "false"}
                for child in callback.named_children
            )
            for callback in callbacks
        )
    ):
        return True
    names = [argument for argument in arguments if argument.type != "block_argument"]
    if any(
        argument.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for argument in names
    ):
        return False
    return len(names) > 1 or any(
        _invalid_argument_name(argument) and _literal_kind(argument) != "nil"
        for argument in names
    )


def _invalid_directory_path(arguments: list[Node]) -> bool:
    if not arguments:
        return True
    path = arguments[0]
    if path.type in {"pair", "hash"}:
        key = _task_hash_key(arguments)
        if key is None:
            return False
        path = key
    return _invalid_argument_name(path) or _literal_kind(path) in {
        "simple_symbol",
        "delimited_symbol",
        "hash_key_symbol",
    }


def _declaration_error(
    node: Node,
    method: str | None,
    arguments: list[Node],
    description: Node | None,
    disabled: set[str],
) -> str | None:
    if method == "undef":
        return "undef of Rake DSL method"
    if method in {"task", "multitask", "file", "file_create", "directory", "rule"} and (
        _invalid_task_arguments(arguments)
        or (method == "directory" and _invalid_directory_path(arguments))
    ):
        return "invalid Rake task arguments"
    if method in {
        "task",
        "multitask",
        "file",
        "file_create",
        "directory",
    } and not _valid_description(description):
        return "invalid Rake description type"
    if method == "namespace" and _invalid_namespace(node, arguments, disabled):
        return "invalid Rake namespace call"
    if method == "desc" and _call_arity(arguments) not in {1, None}:
        return "invalid Rake description arguments"
    return None


def _validate_declaration(
    node: Node,
    method: str | None,
    arguments: list[Node],
    description: Node | None,
    disabled: set[str],
) -> None:
    error = _declaration_error(node, method, arguments, description, disabled)
    if error is not None:
        raise _InvalidDeclarationError(error)


def _task_hash_key(arguments: list[Node]) -> Node | None:
    pairs = arguments
    if len(arguments) == 1 and arguments[0].type == "hash":
        pairs = [
            child for child in arguments[0].named_children if child.type != "comment"
        ]
    keys = _known_hash_keys(pairs)
    if keys is None:
        return None
    ordinary = [key for key in keys if not _order_only_key(key)]
    return ordinary[0] if len(ordinary) == 1 else None


def _task_name(arguments: list[Node]) -> str | None:
    if not arguments:
        return None
    first = arguments[0]
    if first.type in {"pair", "hash"}:
        key = _task_hash_key(arguments)
        if key is None:
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


def _description(arguments: list[Node]) -> Node | None:
    positional, pairs = _task_arguments(arguments)
    node = (
        arguments[0]
        if (len(arguments) == 1 or (pairs is not None and not positional))
        else None
    )
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    return node


def _valid_description(node: Node | None) -> bool:
    # Rake calls strip on truthy descriptions. Symbols and other known non-string
    # literals cannot supply a comment; unknown expressions remain opaque.
    if node is None or _literal_kind(node) in {"nil", "false"}:
        return True
    return not _invalid_argument_name(node) and node.type not in {
        "simple_symbol",
        "delimited_symbol",
    }


def _description_text(node: Node | None) -> str | None:
    return literal(node) if node is not None and node.type == "string" else None


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
                for child in _reachable_children(node)
                if node.type not in {"class", "module", "singleton_class"}
                or child != body
            ]
        pending.extend((child, in_loop or node.type in loops) for child in children)
    return local_control


def _literal_truth(node: Node | None) -> bool | None:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    if node is None:
        return None
    if node.type in {"false", "nil"}:
        return False
    if node.type in {
        "true",
        "integer",
        "float",
        "string",
        "simple_symbol",
        "delimited_symbol",
        "array",
        "hash",
        "regex",
    }:
        return True
    return None


def _binary_children(node: Node) -> list[Node]:
    left = node.child_by_field_name("left")
    operator = node.child_by_field_name("operator")
    if left is None or operator is None:
        return node.named_children
    truth = _literal_truth(left)
    if (operator.type in {"&&", "and"} and truth is False) or (
        operator.type in {"||", "or"} and truth is True
    ):
        # Retain the left operand: its children still execute and may contain
        # controls or DSL overrides, even when its resulting value is truthy.
        return [left]
    return node.named_children


def _reachable_children(node: Node) -> list[Node]:
    if defined_probe(node):
        return []
    if node.type == "binary":
        return _binary_children(node)
    return _conditional_children(node)


def _conditional_children(node: Node) -> list[Node]:
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


def _deferred_call(owner: Node, method: Node, disabled: set[str]) -> bool:
    name = node_text(method)
    if _dsl_receiver(owner):
        return name in _DEFERRED_METHODS - disabled
    receiver = owner.child_by_field_name("receiver")
    key = (
        f"{node_text(receiver).removeprefix('::')}.{name}"
        if receiver is not None
        else ""
    )
    return key in {"Kernel.proc", "Kernel.lambda", "Proc.new"} - disabled


def _load_time_children(node: Node, deferred_calls: set[int]) -> list[Node]:
    if node.type in {"method", "singleton_method", "lambda", "end_block"}:
        return scope_headers(node)
    if node.type in {"block", "do_block"}:
        owner = node.parent
        method = owner.child_by_field_name("method") if owner is not None else None
        if owner is not None and method is not None and owner.id in deferred_calls:
            return []
    return _reachable_children(node)


def _load_declaration_error(node: Node, disabled: set[str]) -> str | None:
    method = node.child_by_field_name("method")
    if node.type != "call" or method is None or not _dsl_receiver(node):
        return None
    name = node_text(method)
    if name in disabled:
        return None
    if not main_scope(node):
        return None
    return _declaration_error(node, name, _arguments(node), None, disabled)


def _load_time_error(root: Node) -> str | None:
    disabled: set[str] = set()
    deferred_calls: set[int] = set()
    bare_constructors = unbound_identifier_ids(root, {"proc", "lambda"})
    pending = [_load_statements(root, disabled)]
    while pending:
        node = next(pending[-1], None)
        if node is None:
            pending.pop()
            continue
        if node.id in bare_constructors and node_text(node) not in disabled:
            return "invalid Proc constructor call"
        method = node.child_by_field_name("method")
        if (
            node.type == "call"
            and method is not None
            and _deferred_call(node, method, disabled)
        ):
            arguments = _arguments(node)
            has_block = node.child_by_field_name("block") is not None or any(
                _literal_kind(child) not in {"nil", "false"}
                for callback in _block_arguments(node)
                for child in callback.named_children
            )
            error = constructor_arguments_error(
                node_text(method),
                [_literal_kind(argument) for argument in arguments],
                _call_arity(arguments),
                has_block=has_block,
            )
            if error is not None:
                return error
            deferred_calls.add(node.id)
        record_override(node, disabled)
        error = _load_declaration_error(node, disabled)
        if error is not None:
            return error
        if _invalid_block_arguments(node):
            return "invalid block argument during loading"
        if node.type == "regex" and any(
            child.type == "interpolation" for child in node.named_children
        ):
            return "unsupported interpolated regexp during loading"
        pending.append(iter(_load_time_children(node, deferred_calls)))
    return None


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
            record_override(node, disabled)
        children = (
            scope_headers(node) if node.type in opaque else _reachable_children(node)
        )
        pending.extend((child, in_begin) for child in children)
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
        pending.extend(_reachable_children(node))
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
                    (child, False) for child in reversed(_reachable_children(node))
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
    # Rake's pending description is global, including across namespaces.
    # An explicit stack avoids Python recursion on deeply nested namespaces.
    disabled: set[str] = set()
    scopes: list[tuple[Iterator[Node], str]] = [(_load_statements(root, disabled), "")]
    description: Node | None = None
    while scopes:
        statements, namespace = scopes.pop()
        node = next(statements, None)
        if node is None:
            continue
        control = _escaping_control(node, include_begin=_in_initializer(node))
        if control in {"return", "redo"}:
            return
        if control in {"break", "next"}:
            continue
        scopes.append((statements, namespace))
        method = _method(node, disabled)
        arguments = _arguments(node)
        _validate_declaration(node, method, arguments, description, disabled)
        if method == "desc":
            description = _description(arguments)
        elif method in {"task", "multitask", "file", "file_create", "directory"}:
            if method in {"task", "multitask"}:
                yield node, namespace, _description_text(description)
            description = None
        elif method == "namespace":
            nested = _namespace_body(node, arguments, namespace)
            if nested is not None:
                qualified, body = nested
                scopes.append((_scope_statements(body), qualified))
            else:
                # An opaque namespace may consume or replace the global comment.
                description = None


def parse_rakefile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Discover literal Rake tasks at file scope or inside literal namespaces.

    Only direct ``task``/``multitask`` calls and pending literal ``desc`` calls
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
    load_error = _load_time_error(root)
    if load_error is not None:
        log.warning("nur: skipping %s (%s)", source_file, load_error)
        return []
    tasks: dict[str, Task] = {}
    descriptions: dict[str, list[str]] = {}
    try:
        for node, namespace, description in _declarations(root):
            task = _make_task(
                descriptions, _arguments(node), namespace, description, source_file
            )
            if task is not None:
                tasks[task.name] = task
    except _InvalidDeclarationError as error:
        log.warning("nur: skipping %s (%s)", source_file, error)
        return []
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
