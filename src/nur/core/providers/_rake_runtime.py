from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_raises import handled_error
from nur.core.providers._rake_syntax import literal, node_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = [
    "empty_for",
    "empty_rescue",
    "load_assignment_error",
    "mutation_error",
    "overridden_method_error",
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
    target = node.child_by_field_name("left") if node.type == "assignment" else None
    if target is None or target.type != "global_variable":
        return None
    name = node_text(target)
    kind = _value_kind(node.child_by_field_name("right"))
    if kind is None:
        return None
    invalid = (
        (name in {"$0", "$PROGRAM_NAME"} and kind != "string")
        or name in {"$stdout", "$stderr", "$>"}
        or (name == "$~" and kind != "nil")
        or (name in {"$/", "$-0", "$,", "$\\", "$-F"} and kind not in {"nil", "string"})
    )
    return (
        "invalid literal value for constrained Ruby global during loading"
        if invalid and not handled_error(node, "TypeError")
        else None
    )


def mutation_error(node: Node, disabled: set[str]) -> str | None:
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
            if not handled_error(node, "NameError"):
                return message
            disabled.discard(marker)
    return None


def load_assignment_error(node: Node) -> str | None:
    """Check assignments that raise only when their code executes during loading."""
    if node.type == "class_variable" and not _class_variable_scope(node):
        return "class variable access from toplevel during loading"
    if error := _constrained_assignment_error(node):
        return error
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
            return "assignment to readonly global during loading"
        if target.type == "class_variable" and not _class_variable_scope(target):
            return "class variable access from toplevel during loading"
    return None


def overridden_method_error(
    key: str, arity: int | None, disabled: set[str]
) -> str | None:
    if f"undef:{key}" in disabled:
        return "call to undefined constructor during loading"
    if f"reader:{key}" in disabled and arity not in {0, None}:
        return "invalid singleton attribute reader call during loading"
    return None
