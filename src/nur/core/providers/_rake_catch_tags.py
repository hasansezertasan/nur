from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import binding_names, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["generated_tag_matches"]


def _callback_scope(owner: Node) -> Node | None:
    if (block := owner.child_by_field_name("block")) is not None:
        return block
    arguments = owner.child_by_field_name("arguments")
    if arguments is None:
        return None
    callbacks = [
        child for child in arguments.named_children if child.type == "block_argument"
    ]
    if len(callbacks) != 1 or len(callbacks[0].named_children) != 1:
        return None
    callback = callbacks[0].named_children[0]
    while (
        callback.type == "parenthesized_statements"
        and len(callback.named_children) == 1
    ):
        callback = callback.named_children[0]
    return (
        callback if callback.type == "lambda" else callback.child_by_field_name("block")
    )


def _target_has_name(target: Node, name: str) -> bool:
    pending = [target]
    while pending:
        node = pending.pop()
        if node.type == "identifier" and node_text(node) == name:
            return True
        pending.extend(node.named_children)
    return False


def _reassigned_before(scope: Node, tag: Node) -> bool:
    pending = [scope]
    while pending:
        node = pending.pop()
        if node.start_byte >= tag.start_byte:
            continue
        field = {
            "assignment": "left",
            "operator_assignment": "left",
            "for": "pattern",
            "rescue": "variable",
        }.get(node.type)
        target = node.child_by_field_name(field) if field else None
        if target is not None and _target_has_name(target, node_text(tag)):
            return True
        pending.extend(node.named_children)
    return False


def generated_tag_matches(owner: Node, tag: Node) -> bool:
    """Recognize an unchanged callback parameter holding catch's yielded tag."""
    arguments = owner.child_by_field_name("arguments")
    positional = (
        [
            child
            for child in arguments.named_children
            if child.type not in {"block_argument", "comment"}
        ]
        if arguments is not None
        else []
    )
    if len(positional) > 1:
        return False
    scope = _callback_scope(owner)
    parameters = scope.child_by_field_name("parameters") if scope is not None else None
    if tag.type != "identifier" or parameters is None or not parameters.named_children:
        return False
    first = parameters.named_children[0]
    if first.type != "identifier" or node_text(first) != node_text(tag):
        return False
    parent = tag.parent
    while parent is not None and parent != scope:
        nested = parent.child_by_field_name("parameters")
        if nested is not None and node_text(tag) in binding_names(nested):
            return False
        parent = parent.parent
    return parent is not None and not _reassigned_before(parent, tag)
