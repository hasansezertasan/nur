from __future__ import annotations

from typing import TYPE_CHECKING

from nur.core.providers._rake_syntax import node_text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = ["invalid_namespace_lambda_parameters"]


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
