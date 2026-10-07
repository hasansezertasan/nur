from __future__ import annotations

import re
from typing import TYPE_CHECKING

from nur.core.providers._rake_overrides import RAKE_METHODS, scope_headers
from nur.core.providers._rake_syntax import literal, node_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node

__all__ = [
    "description_node",
    "description_summary",
    "description_text",
    "pending_description",
    "valid_description",
]


def description_node(
    arguments: list[Node],
    task_arguments: Callable[[list[Node]], tuple[list[Node], list[Node] | None]],
) -> Node | None:
    positional, pairs = task_arguments(arguments)
    node = (
        arguments[0]
        if (len(arguments) == 1 or (pairs is not None and not positional))
        else None
    )
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    return node


def description_summary(comments: list[str]) -> str:
    return " / ".join(
        re.split(
            r"(?<=\w)(\.|!)[ \t]|(\.$|!)|\n", comment, flags=re.ASCII | re.MULTILINE
        )[0]
        for comment in comments
    )


def description_text(node: Node | None) -> str | None:
    return literal(node) if node is not None and node.type == "string" else None


def valid_description(
    node: Node | None,
    literal_kind: Callable[[Node | None], str | None],
    invalid_argument_name: Callable[[Node], bool],
) -> bool:
    # Rake calls strip on truthy descriptions; unknown values remain opaque.
    if node is None or literal_kind(node) in {"nil", "false"}:
        return True
    return not invalid_argument_name(node) and node.type not in {
        "simple_symbol",
        "delimited_symbol",
    }


def pending_description(
    root: Node,
    method: str | None,
    description: Node | None,
    reachable_children: Callable[[Node], list[Node]],
    deferred_call: Callable[[Node, Node], bool],
) -> Node | None:
    """Invalidate metadata that a skipped, reachable declaration may consume."""
    if description is None or method in RAKE_METHODS:
        return description
    pending = [root]
    while pending:
        node = pending.pop()
        method_node = node.child_by_field_name("method")
        if (
            node.type == "call"
            and method_node is not None
            and node_text(method_node) in RAKE_METHODS
        ):
            return None
        if node.type in {"method", "singleton_method", "lambda", "end_block"}:
            pending.extend(scope_headers(node))
            continue
        if node.type in {"block", "do_block"} and node.parent is not None:
            owner = node.parent
            method_node = owner.child_by_field_name("method")
            if method_node is not None and deferred_call(owner, method_node):
                continue
        pending.extend(reachable_children(node))
    return description
