from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import literal, node_text

if TYPE_CHECKING:
    from tree_sitter import Node

__all__ = ["provider_call_error"]


def _provider_parameters(provider: Node) -> list[Node] | None:
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
    return parameters.named_children if parameters is not None else []


def provider_call_error(provider: Node, call: Node) -> tuple[str, str] | None:
    declared = _provider_parameters(provider)
    if declared is None:
        return None
    arguments = call.child_by_field_name("arguments")
    supplied = arguments.named_children if arguments is not None else []
    if any(
        child.type in {"splat_argument", "hash_splat_argument", "forward_argument"}
        for child in supplied
    ):
        return None
    positional = [
        child
        for child in supplied
        if child.type not in {"comment", "pair", "block_argument"}
    ]
    pairs = [child for child in supplied if child.type == "pair"]
    keywords = [child for child in declared if child.type == "keyword_parameter"]
    keyword_rest = any(child.type == "hash_splat_parameter" for child in declared)
    count = len(positional) + bool(pairs and not (keywords or keyword_rest))
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
    required = sum(child.type != "optional_parameter" for child in bindings)
    rest = any(child.type == "splat_parameter" for child in declared)
    names = {
        literal(key)
        for child in pairs
        if (key := child.child_by_field_name("key")) is not None
    }
    missing_keyword = any(
        child.child_by_field_name("value") is None
        and (name := child.child_by_field_name("name")) is not None
        and node_text(name) not in names
        for child in keywords
    )
    if count < required or (not rest and count > len(bindings)) or missing_keyword:
        return (
            "incompatible scoped Rake method arguments during loading",
            "ArgumentError",
        )
    return None
