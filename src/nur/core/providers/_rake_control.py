from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import literal, node_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from tree_sitter import Node

__all__ = ["case_children", "endless_loop_error"]


def _case_value(node: Node | None) -> tuple[str, object] | None:
    if node is None:
        return None
    while node.type in {"pattern", "parenthesized_statements"}:
        if len(node.named_children) != 1:
            return None
        node = node.named_children[0]
    if node.type in {"nil", "true", "false"}:
        return (node.type, None)
    if node.type in {"simple_symbol", "delimited_symbol", "string"}:
        value = literal(node)
        kind = "string" if node.type == "string" else "symbol"
        return (kind, value) if value is not None else None
    return _numeric_case_value(node)


def _numeric_case_value(node: Node) -> tuple[str, object] | None:
    raw = node_text(node).replace("_", "")
    if node.type == "integer":
        try:
            value = int(raw, 0) if raw.startswith("0") and len(raw) > 1 else int(raw)
        except ValueError:
            return None
        return ("number", value)
    if node.type == "float":
        return ("number", float(raw))
    return None


def case_children(node: Node) -> list[Node] | None:
    if node.type != "case":
        return None
    selector = node.child_by_field_name("value")
    value = _case_value(selector)
    if selector is not None and value is None:
        return None
    children = [selector] if selector is not None else []
    for arm in node.named_children:
        if arm.type == "else":
            return [*children, arm]
        if arm.type != "when":
            continue
        patterns = arm.children_by_field_name("pattern")
        for pattern in patterns:
            candidate = _case_value(pattern)
            if candidate is None:
                return None
            children.append(pattern)
            matches = (
                candidate[0] not in {"nil", "false"}
                if selector is None
                else candidate == value
            )
            if matches:
                body = arm.child_by_field_name("body")
                return [*children, body] if body is not None else children
    return children


def _endless_loop(
    node: Node,
    literal_truth: Callable[[Node | None], bool | None],
    reachable_children: Callable[[Node], list[Node]],
) -> bool:
    if node.type not in {"while", "until", "while_modifier", "until_modifier"}:
        return False
    truth = literal_truth(node.child_by_field_name("condition"))
    if truth is None or truth != (node.type in {"while", "while_modifier"}):
        return False
    body = node.child_by_field_name("body")
    pending = [body] if body is not None else []
    harmless = {
        "do",
        "body_statement",
        "comment",
        "nil",
        "true",
        "false",
        "integer",
        "float",
        "string",
        "string_content",
        "simple_symbol",
        "delimited_symbol",
        "symbol_content",
        "next",
        "redo",
        "if",
        "unless",
        "if_modifier",
        "unless_modifier",
        "then",
        "else",
    }
    while pending:
        child = pending.pop()
        if child.type not in harmless:
            return False
        pending.extend(reachable_children(child))
    return True


def endless_loop_error(
    node: Node,
    literal_truth: Callable[[Node | None], bool | None],
    reachable_children: Callable[[Node], list[Node]],
) -> str | None:
    return (
        "statically endless loop during loading"
        if _endless_loop(node, literal_truth, reachable_children)
        else None
    )
