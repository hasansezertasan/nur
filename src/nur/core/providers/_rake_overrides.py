from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import (
    is_self,
    literal,
    node_text,
    unbound_identifier_ids,
)

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = [
    "DEFERRED_METHODS",
    "SINGLETON_MUTATORS",
    "main_scope",
    "reader_call",
    "receiver_name",
    "record_override",
    "scope_headers",
    "singleton_class_receiver",
]

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


DEFERRED_METHODS = frozenset(_METHODS - {"namespace"})
SINGLETON_MUTATORS = frozenset({
    "define_method",
    "define_singleton_method",
    "alias_method",
    "undef_method",
    "remove_method",
    "attr",
    "attr_reader",
    "attr_accessor",
})


def receiver_name(node: Node | None) -> str:
    while node is not None and node.type == "parenthesized_statements":
        children = [child for child in node.named_children if child.type != "comment"]
        node = children[0] if len(children) == 1 else None
    return node_text(node).removeprefix("::") if node is not None else ""


def reader_call(node: Node, disabled: set[str]) -> bool:
    method = node.child_by_field_name("method")
    if method is None:
        return False
    receiver = node.child_by_field_name("receiver")
    name = node_text(method)
    key = (
        name
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.{name}"
    )
    return f"reader:{key}" in disabled


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
    disabled.discard(f"reader:{name}")


def _set_singleton_override(name: str, disabled: set[str]) -> None:
    _set_override(name, disabled)
    disabled.add(f"singleton:{name}")


def _instance_override(node: Node, name: str, disabled: set[str]) -> None:
    prefix, singleton_scope = _constructor_scope(node)
    if prefix is not None and singleton_scope:
        _set_singleton_override(f"{prefix}.{name}", disabled)
    elif main_scope(node) and name in {
        "proc",
        "lambda",
        "define_singleton_method",
        "singleton_class",
    }:
        disabled.add(f"inherited:{name}")
        if f"singleton:{name}" not in disabled:
            _set_override(name, disabled)
    elif prefix == "Kernel" and name in {
        "proc",
        "lambda",
        "define_singleton_method",
        "singleton_class",
    }:
        disabled.update({name, f"inherited:{name}"})


def _ordinary_override(node: Node, disabled: set[str]) -> None:
    name = node.child_by_field_name("name")
    if name is not None:
        _instance_override(node, literal(name) or node_text(name), disabled)


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
            "singleton_class",
            "Kernel.proc",
            "Kernel.lambda",
            "Proc.new",
        }:
            disabled.update({name, f"undef:{name}"})


def _self_override(node: Node, name: str, disabled: set[str]) -> None:
    prefix, _ = _constructor_scope(node)
    if prefix is not None:
        _set_singleton_override(f"{prefix}.{name}", disabled)
    elif main_scope(node):
        _set_singleton_override(name, disabled)


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
            _set_singleton_override(
                f"{node_text(owner).removeprefix('::')}.{node_text(name_node)}",
                disabled,
            )
    if node.type in {"method", "alias"}:
        _ordinary_override(node, disabled)
    if node.type == "undef":
        _record_undef(node, disabled)
    _dynamic_override(node, disabled)


def singleton_class_receiver(node: Node | None, disabled: set[str]) -> bool:
    if "singleton_class" in disabled:
        return False
    while (
        node is not None
        and node.type == "parenthesized_statements"
        and len(node.named_children) == 1
    ):
        node = node.named_children[0]
    if node is None:
        return False
    if node.type == "identifier" and node_text(node) == "singleton_class":
        root = node
        while root.parent is not None:
            root = root.parent
        return node.id in unbound_identifier_ids(root, {"singleton_class"})
    receiver = node.child_by_field_name("receiver")
    method = node.child_by_field_name("method")
    if node.type != "call" or method is None:
        return False
    arguments = node.child_by_field_name("arguments")
    has_arguments = arguments is not None and any(
        child.type != "comment" for child in arguments.named_children
    )
    return (
        (receiver is None or is_self(receiver))
        and node_text(method) == "singleton_class"
        and not has_arguments
    )


def _dynamic_override(node: Node, disabled: set[str]) -> None:
    receiver = node.child_by_field_name("receiver")
    singleton_receiver = singleton_class_receiver(receiver, disabled)
    if node.type == "call" and (
        receiver is None or is_self(receiver) or singleton_receiver
    ):
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
        mutators = {"define_method", "define_singleton_method"}
        if singleton_receiver:
            mutators.update(SINGLETON_MUTATORS)
        if method is not None and node_text(method) in mutators - disabled:
            _record_mutation(
                node,
                node_text(method),
                arguments,
                disabled,
                singleton_receiver=singleton_receiver,
            )


def _record_mutation(
    node: Node,
    method: str,
    arguments: list[Node],
    disabled: set[str],
    *,
    singleton_receiver: bool,
) -> None:
    if method in {"attr", "attr_reader", "attr_accessor"}:
        _record_attribute_readers(node, arguments, disabled)
        return
    if method == "define_singleton_method" and singleton_receiver:
        return
    if method == "remove_method":
        _record_removal(node, arguments, disabled)
        return
    if method == "undef_method":
        prefix, _ = _constructor_scope(node)
        if prefix is None and not main_scope(node):
            return
        for argument in arguments:
            if (name := literal(argument)) is not None:
                key = f"{prefix}.{name}" if prefix else name
                disabled.update({key, f"undef:{key}"})
        return
    if arguments and (defined_name := literal(arguments[0])) is not None:
        if method == "define_singleton_method" or singleton_receiver:
            _self_override(node, defined_name, disabled)
        else:
            _instance_override(node, defined_name, disabled)


def _record_attribute_readers(
    node: Node, arguments: list[Node], disabled: set[str]
) -> None:
    prefix, _ = _constructor_scope(node)
    if prefix is None and not main_scope(node):
        return
    for argument in arguments:
        if (name := literal(argument)) is not None:
            key = f"{prefix}.{name}" if prefix else name
            _set_singleton_override(key, disabled)
            disabled.add(f"reader:{key}")


def _record_removal(node: Node, arguments: list[Node], disabled: set[str]) -> None:
    prefix, _ = _constructor_scope(node)
    if prefix is None and not main_scope(node):
        return
    for argument in arguments:
        name = literal(argument)
        if name is None:
            continue
        key = f"{prefix}.{name}" if prefix else name
        if f"singleton:{key}" not in disabled:
            continue
        disabled.difference_update({
            key,
            f"singleton:{key}",
            f"reader:{key}",
            f"undef:{key}",
        })
        if key in {"define_method", "Kernel.proc", "Kernel.lambda"}:
            disabled.update({key, f"undef:{key}"})
        elif f"inherited:{key}" in disabled:
            disabled.add(key)


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
