from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_overrides import main_scope, receiver_name
from nur.core.providers._rake_syntax import is_self, node_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = [
    "block_arguments",
    "constructor_arguments_error",
    "dsl_receiver",
    "invalid_namespace_lambda_parameters",
    "namespace_callbacks",
]

_METHOD_BODY_ARITY = 2


def _callback_nodes(root: Node) -> Iterator[Node]:
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        if (
            node == root
            or node.type
            not in {
                "lambda",
                "block",
                "do_block",
                "method",
                "singleton_method",
                "class",
                "module",
                "singleton_class",
            }
            or (root.type == "lambda" and node == root.child_by_field_name("body"))
        ):
            pending.extend(node.named_children)


def invalid_namespace_lambda_parameters(callback: Node) -> bool:
    parameters = callback.child_by_field_name("parameters")
    if parameters is None:
        # Implicit numbered/it parameters may establish an arity dynamically.
        arities = [
            int(name[1]) if name.startswith("_") else 1
            for child in _callback_nodes(callback)
            if (name := node_text(child))
            in {"it", "_1", "_2", "_3", "_4", "_5", "_6", "_7", "_8", "_9"}
        ]
        return not arities or max(arities) > 1
    required = 0
    maximum = 0
    rest = False
    for index, parameter in enumerate(parameters.children):
        if not parameter.is_named or parameters.field_name_for_child(index) == "locals":
            continue
        if parameter.type == "keyword_parameter":
            if parameter.child_by_field_name("value") is None:
                return True
        elif parameter.type == "splat_parameter":
            rest = True
        elif parameter.type not in {
            "comment",
            "hash_splat_parameter",
            "block_parameter",
            "block_local_variable",
        }:
            maximum += 1
            required += parameter.type != "optional_parameter"
    return required > 1 or (maximum < 1 and not rest)


_INVALID_METHOD_NAME_KINDS = {
    "nil",
    "true",
    "false",
    "integer",
    "float",
    "array",
    "hash",
    "regex",
    "range",
    "lambda",
}


def _simple_constructor_error(
    name: str, kinds: list[str | None], arity: int | None, *, has_block: bool
) -> str | None:
    if name == "attr" and kinds[1:] in (["true"], ["false"]):
        kinds = kinds[:1]
    if name in {
        "alias_method",
        "undef_method",
        "remove_method",
        "attr",
        "attr_reader",
        "attr_accessor",
    }:
        if name == "alias_method" and arity not in {2, None}:
            return "invalid method alias call"
        return (
            "invalid method alias or removal name"
            if any(kind in _INVALID_METHOD_NAME_KINDS for kind in kinds)
            else None
        )
    return (
        "invalid Proc constructor call"
        if arity not in {0, None} or not has_block
        else None
    )


def constructor_arguments_error(
    name: str,
    kinds: list[str | None],
    arity: int | None,
    *,
    has_block: bool,
    is_reader: bool = False,
) -> str | None:
    if (
        name
        in {
            "alias_method",
            "undef_method",
            "remove_method",
            "attr",
            "attr_reader",
            "attr_accessor",
            "proc",
            "lambda",
            "new",
        }
        and not is_reader
    ):
        return _simple_constructor_error(name, kinds, arity, has_block=has_block)
    if is_reader or name not in {"define_method", "define_singleton_method"}:
        return None
    if arity not in {1, 2, None} or (arity == 1 and not has_block):
        return "invalid method definition constructor call"

    if kinds and kinds[0] in _INVALID_METHOD_NAME_KINDS:
        return "invalid method definition name"
    if (
        arity == _METHOD_BODY_ARITY
        and len(kinds) == _METHOD_BODY_ARITY
        and kinds[1]
        in (_INVALID_METHOD_NAME_KINDS - {"lambda"})
        | {"string", "simple_symbol", "delimited_symbol"}
    ):
        return "invalid method definition body"
    return None


def block_arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    return (
        [child for child in arguments.named_children if child.type == "block_argument"]
        if arguments is not None
        else []
    )


def dsl_receiver(node: Node) -> bool:
    receiver = node.child_by_field_name("receiver")
    return receiver is None or is_self(receiver)


def namespace_callbacks(node: Node, disabled: set[str]) -> set[int]:
    method = node.child_by_field_name("method")
    if (
        method is None
        or node_text(method) != "namespace"
        or "namespace" in disabled
        or not main_scope(node)
        or not dsl_receiver(node)
    ):
        return set()
    return {
        identity
        for argument in block_arguments(node)
        for callback in argument.named_children
        for identity in _inline_callback_ids(callback, disabled)
    }


def _inline_callback_ids(callback: Node, disabled: set[str]) -> set[int]:
    while (
        callback.type == "parenthesized_statements"
        and len(callback.named_children) == 1
    ):
        callback = callback.named_children[0]
    if callback.type == "lambda":
        return {callback.id}
    method = callback.child_by_field_name("method")
    if method is None:
        return set()
    receiver = callback.child_by_field_name("receiver")
    name = node_text(method)
    key = (
        name
        if receiver is None or is_self(receiver)
        else f"{receiver_name(receiver)}.{name}"
    )
    block = callback.child_by_field_name("block")
    if (
        key
        not in {"proc", "lambda", "Kernel.proc", "Kernel.lambda", "Proc.new"} - disabled
        or block is None
    ):
        return set()
    return {callback.id, block.id}
