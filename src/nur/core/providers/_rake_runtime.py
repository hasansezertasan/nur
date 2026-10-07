from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_overrides import RAKE_METHODS, main_scope
from nur.core.providers._rake_parameters import provider_call_error
from nur.core.providers._rake_raises import handled_load_error
from nur.core.providers._rake_syntax import is_self, literal, node_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from tree_sitter import Node

__all__ = [
    "empty_for",
    "empty_rescue",
    "load_assignment_error",
    "load_control_error",
    "mutation_error",
    "overridden_method_error",
    "scope_dsl_error",
]

_READONLY_GLOBALS = {
    "$?",
    "$!",
    "$:",
    "$<",
    '$"',
    "$$",
    "$*",
    "$LOAD_PATH",
    "$LOADED_FEATURES",
    "$FILENAME",
    "$-I",
    "$-W",
    "$-a",
    "$-l",
    "$-p",
}

_READONLY_TRUTHY_GLOBALS = _READONLY_GLOBALS - {"$?", "$!", "$-a", "$-l", "$-p"}


def _assignment_targets(node: Node) -> Iterator[Node]:
    field = {
        "assignment": "left",
        "operator_assignment": "left",
        "for": "pattern",
        "rescue": "variable",
    }.get(node.type)
    target = node.child_by_field_name(field) if field else None
    pending = [target] if target is not None else []
    while pending:
        target = pending.pop()
        if target.type in {
            "left_assignment_list",
            "destructured_left_assignment",
            "rest_assignment",
            "exception_variable",
        }:
            pending.extend(target.named_children)
        else:
            yield target


def _class_variable_scope(node: Node) -> bool:
    child, parent = node, node.parent
    while parent is not None:
        if parent.type in {"class", "module"} and child == parent.child_by_field_name(
            "body"
        ):
            return True
        child, parent = parent, parent.parent
    return False


def empty_for(node: Node) -> bool:
    if node.type != "for":
        return False
    value = node.child_by_field_name("value")
    while value is not None and value.type in {"in", "parenthesized_statements"}:
        children = [child for child in value.named_children if child.type != "comment"]
        value = children[0] if len(children) == 1 else None
    return (
        value is not None
        and value.type == "array"
        and not any(child.type != "comment" for child in value.named_children)
    )


def empty_rescue(node: Node) -> bool:
    if node.type != "rescue" or node.parent is None:
        return False
    for child in node.parent.named_children:
        if child == node:
            return True
        if child.type == "rescue":
            return True
        if (
            child.type not in {"comment", "nil", "true", "false", "integer", "float"}
            and literal(child) is None
        ):
            return False
    return False


_LITERAL_KINDS = {
    "nil",
    "true",
    "false",
    "integer",
    "float",
    "string",
    "simple_symbol",
    "delimited_symbol",
    "array",
    "hash",
    "regex",
    "range",
    "lambda",
}


def _unary_kind(node: Node) -> str | None:
    operator = node.child_by_field_name("operator")
    operand = node.child_by_field_name("operand")
    if operator is None or operand is None:
        return None
    if operator.type in {"!", "not"}:
        return "true"
    if operator.type in {"+", "-"} and operand.type in {"integer", "float", "string"}:
        return operand.type
    return "integer" if operator.type == "~" and operand.type == "integer" else None


def _value_kind(node: Node | None) -> str | None:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    if node is not None and node.type == "unary":
        return _unary_kind(node)
    return node.type if node is not None and node.type in _LITERAL_KINDS else None


def _constrained_assignment_error(node: Node) -> str | None:
    target = (
        node.child_by_field_name("left")
        if node.type in {"assignment", "operator_assignment"}
        else None
    )
    if target is None or target.type != "global_variable":
        return None
    name = node_text(target)
    if node.type == "operator_assignment":
        operator = node.child_by_field_name("operator")
        if (
            operator is None
            or node_text(operator) != "&&="
            or name not in {"$stdout", "$stderr", "$>", "$0", "$PROGRAM_NAME"}
        ):
            return None
    kind = _value_kind(node.child_by_field_name("right"))
    if kind is None:
        return None
    invalid = (
        (name in {"$0", "$PROGRAM_NAME"} and kind != "string")
        or name in {"$stdout", "$stderr", "$>"}
        or (name == "$~" and kind != "nil")
        or (name in {"$;", "$-F"} and kind not in {"nil", "string", "regex"})
        or (name in {"$/", "$-0", "$,", "$\\", "$-i"} and kind not in {"nil", "string"})
        or (name == "$." and kind not in {"integer", "float"})
    )
    return (
        "invalid literal value for constrained Ruby global during loading"
        if invalid
        else None
    )


def mutation_error(
    node: Node, disabled: set[str], raised_scopes: dict[int, str | None]
) -> str | None:
    messages = {
        "invalid:visibility": (
            "visibility change to singleton-only Rake method during loading"
        ),
        "invalid:remove_method": (
            "invalid removal of inherited Rake DSL method during loading"
        ),
        "invalid:alias_method": "alias of deleted singleton method during loading",
        "invalid:lexical_alias": (
            "lexical alias of singleton-only Rake DSL method during loading"
        ),
    }
    for marker, message in messages.items():
        if marker in disabled:
            error = handled_load_error(node, message, "NameError", raised_scopes)
            disabled.discard(marker)
            if error is not None:
                return error
    return None


def _falsey_exception_global(node: Node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in {"rescue", "rescue_modifier", "ensure"}:
            return False
        parent = parent.parent
    return True


def load_assignment_error(
    node: Node, raised_scopes: dict[int, str | None]
) -> str | None:
    """Check assignments that raise only when their code executes during loading."""
    if node.type == "class_variable" and not _class_variable_scope(node):
        return handled_load_error(
            node,
            "class variable access from toplevel during loading",
            "RuntimeError",
            raised_scopes,
        )
    if error := _constrained_assignment_error(node):
        return handled_load_error(node, error, "TypeError", raised_scopes)
    if empty_for(node) or empty_rescue(node):
        return None
    for target in _assignment_targets(node):
        if target.type == "global_variable" and node_text(target) in _READONLY_GLOBALS:
            operator = node.child_by_field_name("operator")
            if (
                operator is not None
                and node_text(operator) == "||="
                and node_text(target) in _READONLY_TRUTHY_GLOBALS
            ):
                continue
            if (
                operator is not None
                and node_text(operator) == "&&="
                and node_text(target) == "$!"
                and _falsey_exception_global(node)
            ):
                continue
            return handled_load_error(
                node,
                "assignment to readonly global during loading",
                "NameError",
                raised_scopes,
            )
        if target.type == "class_variable" and not _class_variable_scope(target):
            return handled_load_error(
                node,
                "class variable access from toplevel during loading",
                "RuntimeError",
                raised_scopes,
            )
    return None


def overridden_method_error(
    key: str, arity: int | None, disabled: set[str]
) -> str | None:
    if f"undef:{key}" in disabled:
        return "call to undefined constructor during loading"
    if f"reader:{key}" in disabled and arity not in {0, None}:
        return "invalid singleton attribute reader call during loading"
    return None


def _scope_method_provider(
    node: Node, name: str, constructor_error: Callable[[Node], str | None]
) -> Node | None:
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        method = node.child_by_field_name("name")
        matches = (
            owner is not None
            and node_text(owner) == "self"
            and (method is not None and node_text(method) == name)
        )
        return node if matches else None
    method = node.child_by_field_name("method")
    receiver = node.child_by_field_name("receiver")
    valid_receiver = receiver is None or is_self(receiver)
    if (
        node.type != "call"
        or method is None
        or constructor_error(node) is not None
        or not valid_receiver
    ):
        return None
    arguments = node.child_by_field_name("arguments")
    if node_text(method) == "extend":
        matches = arguments is not None and any(
            node_text(argument) in {"Rake::DSL", "::Rake::DSL"}
            for argument in arguments.named_children
        )
        return node if matches else None
    if node_text(method) in {"class_eval", "module_eval", "class_exec", "module_exec"}:
        block = node.child_by_field_name("block")
        body = block.child_by_field_name("body") if block is not None else None
        for child in body.named_children if body is not None else []:
            provider = _scope_method_provider(child, name, constructor_error)
            if provider is not None:
                return provider
        return None
    matches = (
        node_text(method) == "define_singleton_method"
        and arguments is not None
        and bool(arguments.named_children)
        and literal(arguments.named_children[0]) == name
    )
    return node if matches else None


def scope_dsl_error(
    node: Node,
    name: str,
    reachable_children: Callable[[Node], list[Node]],
    constructor_error: Callable[[Node], str | None],
    literal_truth: Callable[[Node | None], bool | None],
) -> tuple[str, str] | None:
    scope = node.parent
    while scope is not None and scope.type not in {
        "class",
        "module",
        "singleton_class",
    }:
        scope = scope.parent
    if scope is None:
        return None
    body = scope.child_by_field_name("body")
    pending = list(body.named_children) if body is not None else []
    while pending:
        child = pending.pop()
        if child.start_byte >= node.start_byte:
            continue
        provider = _scope_method_provider(child, name, constructor_error)
        if provider is not None:
            return provider_call_error(provider, node)
        if child.type not in {
            "class",
            "module",
            "singleton_class",
            "method",
            "singleton_method",
            "block",
            "do_block",
        }:
            pending.extend(_provider_children(child, reachable_children, literal_truth))
    return "missing Rake DSL method in non-main scope during loading", "NoMethodError"


def _provider_children(
    node: Node,
    reachable_children: Callable[[Node], list[Node]],
    literal_truth: Callable[[Node | None], bool | None],
) -> list[Node]:
    condition = node.child_by_field_name("condition")
    if condition is not None and literal_truth(condition) is None:
        return [condition]
    if node.type == "binary":
        operator = node.child_by_field_name("operator")
        left = node.child_by_field_name("left")
        if (
            operator is not None
            and operator.type in {"&&", "||", "and", "or"}
            and left is not None
            and literal_truth(left) is None
        ):
            return [left]
    return reachable_children(node)


def load_control_error(node: Node, raised_scopes: dict[int, str | None]) -> str | None:
    if node.type == "super":
        return handled_load_error(
            node, "super outside method during loading", "NoMethodError", raised_scopes
        )
    if (
        node.type == "undef"
        and main_scope(node)
        and any(
            (literal(child) or node_text(child)) in RAKE_METHODS
            for child in node.named_children
        )
    ):
        return "undef of Rake DSL method"
    return None
