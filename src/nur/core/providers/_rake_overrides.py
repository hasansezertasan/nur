from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import is_self, literal, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["main_scope", "record_override", "scope_headers"]

_METHODS = {
    "task",
    "multitask",
    "file",
    "file_create",
    "directory",
    "rule",
    "desc",
    "namespace",
    "proc",
    "lambda",
    "define_method",
    "define_singleton_method",
}


def _constructor_scope(node: Node) -> tuple[str | None, bool]:
    parent = node.parent
    singleton_scope = False
    while parent is not None:
        if parent.type in {"class", "module", "singleton_class"}:
            field = "value" if parent.type == "singleton_class" else "name"
            owner = parent.child_by_field_name(field)
            singleton_scope = singleton_scope or parent.type == "singleton_class"
            if parent.type == "singleton_class" and is_self(owner):
                parent = parent.parent
                continue
            name = node_text(owner).removeprefix("::") if owner is not None else ""
            return name if name in {"Proc", "Kernel"} else None, singleton_scope
        parent = parent.parent
    return None, singleton_scope


def _set_override(name: str, disabled: set[str]) -> None:
    disabled.add(name)
    disabled.discard(f"undef:{name}")


def _instance_override(node: Node, name: str, disabled: set[str]) -> None:
    prefix, singleton_scope = _constructor_scope(node)
    if prefix is not None and singleton_scope:
        _set_override(f"{prefix}.{name}", disabled)
    elif main_scope(node) and name in {"proc", "lambda", "define_singleton_method"}:
        _set_override(name, disabled)
    elif prefix == "Kernel" and name in {"proc", "lambda", "define_singleton_method"}:
        disabled.add(name)


def _ordinary_override(node: Node, disabled: set[str]) -> None:
    name = node.child_by_field_name("name")
    if name is not None:
        _instance_override(node, node_text(name), disabled)


def _record_undef(node: Node, disabled: set[str]) -> None:
    prefix, singleton_scope = _constructor_scope(node)
    for child in node.named_children:
        name = literal(child) or node_text(child)
        if prefix is not None and singleton_scope:
            name = f"{prefix}.{name}"
        elif not (main_scope(node) or prefix == "Kernel"):
            continue
        if name in {
            "proc",
            "lambda",
            "define_singleton_method",
            "Kernel.proc",
            "Kernel.lambda",
            "Proc.new",
        }:
            disabled.update({name, f"undef:{name}"})


def _self_override(node: Node, name: str, disabled: set[str]) -> None:
    prefix, _ = _constructor_scope(node)
    if prefix is not None:
        _set_override(f"{prefix}.{name}", disabled)
    elif main_scope(node):
        _set_override(name, disabled)


def record_override(node: Node, disabled: set[str]) -> None:
    # Direct singleton definitions replace the methods Rake extends main with.
    # A singleton-class body can replace any of them; do not guess its effects.
    if node.type == "singleton_class":
        value = node.child_by_field_name("value")
        if is_self(value) and main_scope(node):
            disabled.update(_METHODS)
    if node.type == "singleton_method":
        owner = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if is_self(owner) and name_node is not None:
            _self_override(node, node_text(name_node), disabled)
        elif (
            owner is not None
            and name_node is not None
            and node_text(owner) in {"Kernel", "::Kernel", "Proc", "::Proc"}
        ):
            _set_override(
                f"{node_text(owner).removeprefix('::')}.{node_text(name_node)}",
                disabled,
            )
    if node.type == "method":
        _ordinary_override(node, disabled)
    if node.type == "undef":
        _record_undef(node, disabled)
    _dynamic_override(node, disabled)


def _dynamic_override(node: Node, disabled: set[str]) -> None:
    receiver = node.child_by_field_name("receiver")
    if node.type == "call" and (receiver is None or is_self(receiver)):
        method = node.child_by_field_name("method")
        argument_list = node.child_by_field_name("arguments")
        arguments = (
            [
                child
                for child in argument_list.named_children
                if child.type not in {"comment", "block_argument"}
            ]
            if argument_list is not None
            else []
        )
        if (
            method is not None
            and node_text(method) in {"define_method", "define_singleton_method"}
            and node_text(method) not in disabled
            and arguments
            and (defined_name := literal(arguments[0])) is not None
        ):
            if node_text(method) == "define_singleton_method":
                _self_override(node, defined_name, disabled)
            else:
                _instance_override(node, defined_name, disabled)


def scope_headers(node: Node) -> list[Node]:
    return [
        child
        for field in ("object", "value", "name", "superclass")
        if (child := node.child_by_field_name(field)) is not None
    ]


def main_scope(node: Node) -> bool:
    child, parent = node, node.parent
    while parent is not None:
        if parent.type in {
            "class",
            "module",
            "singleton_class",
        } and child not in scope_headers(parent):
            return False
        child, parent = parent, parent.parent
    return True
