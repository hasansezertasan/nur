from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_callbacks import implicit_parameter_arity
from nur.core.providers._rake_syntax import literal, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["provider_call_error"]


def _provider_parameters(provider: Node) -> tuple[list[Node], int] | None:
    definition = provider
    if provider.type == "call":
        method = provider.child_by_field_name("method")
        if method is None or node_text(method) != "define_singleton_method":
            return None
        block = provider.child_by_field_name("block")
        if block is None:
            return None
        definition = block
    parameters = definition.child_by_field_name("parameters")
    if parameters is None:
        implicit = (
            implicit_parameter_arity(definition)
            if definition.type in {"block", "do_block"}
            else 0
        )
        return [], implicit
    return (
        [
            child
            for index, child in enumerate(parameters.children)
            if child.is_named and parameters.field_name_for_child(index) != "locals"
        ],
        0,
    )


def _keyword_incompatible(declared: list[Node], pairs: list[Node]) -> bool:
    if pairs and any(node_text(child) == "**nil" for child in declared):
        return True
    keywords = [child for child in declared if child.type == "keyword_parameter"]
    if not keywords:
        return False
    names = {
        literal(key)
        for child in pairs
        if (key := child.child_by_field_name("key")) is not None
    }
    if None in names:
        return False
    declared_names = {
        node_text(name)
        for child in keywords
        if (name := child.child_by_field_name("name")) is not None
    }
    required_names = {
        node_text(name)
        for child in keywords
        if child.child_by_field_name("value") is None
        and (name := child.child_by_field_name("name")) is not None
    }
    keyword_rest = any(child.type == "hash_splat_parameter" for child in declared)
    return bool(required_names - names) or (
        not keyword_rest and bool(names - declared_names)
    )


def provider_call_error(provider: Node, call: Node) -> tuple[str, str] | None:
    signature = _provider_parameters(provider)
    if signature is None:
        return None
    declared, implicit = signature
    arguments = call.child_by_field_name("arguments")
    supplied = arguments.named_children if arguments is not None else []
    if any(
        child.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for child in supplied
    ):
        return None
    pairs = [child for child in supplied if child.type == "pair"]
    keywords = [child for child in declared if child.type == "keyword_parameter"]
    keyword_rest = any(child.type == "hash_splat_parameter" for child in declared)
    count = sum(
        child.type not in {"comment", "pair", "block_argument"} for child in supplied
    ) + bool(pairs and not (keywords or keyword_rest))
    bindings = [
        child
        for child in declared
        if child.type
        not in {
            "comment",
            "keyword_parameter",
            "hash_splat_parameter",
            "block_parameter",
            "splat_parameter",
        }
    ]
    required = max(
        sum(child.type != "optional_parameter" for child in bindings), implicit
    )
    rest = any(child.type == "splat_parameter" for child in declared)
    if (
        count < required
        or (not rest and count > max(len(bindings), implicit))
        or _keyword_incompatible(declared, pairs)
    ):
        return (
            "incompatible scoped Rake method arguments during loading",
            "ArgumentError",
        )
    return None
