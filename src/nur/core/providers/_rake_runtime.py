from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import node_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = ["load_assignment_error"]

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
    target = node.child_by_field_name("left")
    pending = [target] if target is not None else []
    while pending:
        target = pending.pop()
        if target.type in {
            "left_assignment_list",
            "destructured_left_assignment",
            "rest_assignment",
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


def load_assignment_error(node: Node) -> str | None:
    """Check assignments that raise only when their code executes during loading."""
    if node.type not in {"assignment", "operator_assignment"}:
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
