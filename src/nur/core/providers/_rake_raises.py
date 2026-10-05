from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_overrides import TERMINATING_METHODS, receiver_name
from nur.core.providers._rake_syntax import is_self, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["load_raise_error"]

_ERROR_PARENTS = {
    "RuntimeError": "StandardError",
    "ArgumentError": "StandardError",
    "TypeError": "StandardError",
    "NameError": "StandardError",
    "NoMethodError": "NameError",
    "RegexpError": "StandardError",
    "ZeroDivisionError": "StandardError",
    "StandardError": "Exception",
    "SyntaxError": "ScriptError",
    "LoadError": "ScriptError",
    "NotImplementedError": "ScriptError",
    "ScriptError": "Exception",
    "SystemExit": "Exception",
    "Interrupt": "SignalException",
    "SignalException": "Exception",
}


def _arguments(node: Node) -> list[Node]:
    argument_list = node.child_by_field_name("arguments")
    return (
        [
            child
            for child in argument_list.named_children
            if child.type not in {"comment", "block_argument"}
        ]
        if argument_list is not None
        else []
    )


def _raised_kind(node: Node) -> str:
    arguments = _arguments(node)
    if not arguments or arguments[0].type == "string":
        return "RuntimeError"
    first = arguments[0]
    if first.type in {"constant", "scope_resolution"}:
        return receiver_name(first)
    if (
        first.type == "call"
        and (method := first.child_by_field_name("method")) is not None
        and node_text(method) == "new"
    ):
        return receiver_name(first.child_by_field_name("receiver"))
    return (
        "TypeError"
        if first.type in {"nil", "true", "false", "integer", "float", "array", "hash"}
        else "Exception"
    )


def _exit_kind(node: Node, name: str) -> str:
    arguments = _arguments(node)
    if any(
        argument.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for argument in arguments
    ):
        return "SystemExit"
    if len(arguments) > 1:
        return "ArgumentError"
    if not arguments:
        return "SystemExit"
    invalid = {
        "nil",
        "array",
        "hash",
        "pair",
        "simple_symbol",
        "delimited_symbol",
        "regex",
        "range",
    }
    invalid.update(
        {"integer", "float", "true", "false"} if name == "abort" else {"string"}
    )
    return "TypeError" if arguments[0].type in invalid else "SystemExit"


def _ancestors(kind: str) -> set[str]:
    result = {kind, "Exception"}
    while kind in _ERROR_PARENTS:
        kind = _ERROR_PARENTS[kind]
        result.add(kind)
    return result


def _rescue_matches(node: Node, kind: str) -> bool:
    exceptions = node.child_by_field_name("exceptions")
    names = (
        {"StandardError"}
        if exceptions is None
        else {
            receiver_name(child)
            for child in exceptions.named_children
            if child.type in {"constant", "scope_resolution"}
        }
    )
    return bool(names & _ancestors(kind))


def _handled_raise(node: Node, kind: str) -> bool:
    child, parent = node, node.parent
    while parent is not None:
        if (
            parent.type == "rescue_modifier"
            and child == parent.child_by_field_name("body")
            and "StandardError" in _ancestors(kind)
        ):
            return True
        if parent.type in {"begin", "body_statement"} and child.type not in {
            "rescue",
            "ensure",
            "else",
        }:
            following = False
            for sibling in parent.named_children:
                if (
                    following
                    and sibling.type == "rescue"
                    and _rescue_matches(sibling, kind)
                ):
                    return True
                following = following or sibling == child
        child, parent = parent, parent.parent
    return False


def load_raise_error(
    node: Node, disabled: set[str], bare_raises: set[int]
) -> str | None:
    method = node.child_by_field_name("method")
    name = (
        node_text(method)
        if method is not None
        else node_text(node)
        if node.id in bare_raises
        else ""
    )
    if name not in TERMINATING_METHODS:
        return None
    receiver = node.child_by_field_name("receiver")
    key = (
        name
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.{name}"
    )
    known = TERMINATING_METHODS | {f"Kernel.{method}" for method in TERMINATING_METHODS}
    if key not in known or key in disabled:
        return None
    exit_call = name in {"exit", "abort"}
    kind = _exit_kind(node, name) if exit_call else _raised_kind(node)
    if _handled_raise(node, kind):
        return None
    return (
        "unhandled process exit during loading"
        if exit_call
        else "uncaught raise during loading"
    )
