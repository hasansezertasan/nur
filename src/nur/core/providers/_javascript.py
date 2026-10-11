"""Tree-sitter JavaScript primitives shared by the Grunt and Gulp providers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import tree_sitter_javascript
from tree_sitter import Language, Parser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

__all__ = ["LANGUAGE", "node_text", "parse", "plain_string", "walk"]

log = logging.getLogger("nur")
LANGUAGE = Language(tree_sitter_javascript.language())


def node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8")


def plain_string(node: Node | None) -> str | None:
    """Read a plain 'single-quoted'/"double-quoted" literal without JS evaluation."""
    if node is None or node.type != "string":
        return None
    if any(child.type != "string_fragment" for child in node.named_children):
        return None
    # A `string` node's text always includes both quotes (`''` at minimum).
    value = node_text(node)[1:-1]
    # Escapes need JS string decoding to reproduce; omit such names instead of
    # guessing, mirroring the rake provider's treatment of escaped names.
    return None if "\\" in value else value


def walk(root: Node) -> Iterator[Node]:
    """Yield ``root`` and its descendants in source order (pre-order).

    Yields:
        Each node, starting with ``root`` itself.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def parse(text: str, source_file: str) -> Node | None:
    """Parse JavaScript *text*; on syntax errors log a warning and return None."""
    root = Parser(LANGUAGE).parse(text.encode("utf-8")).root_node
    if root.has_error:
        log.warning("nur: skipping %s (unparsable JavaScript syntax)", source_file)
        return None
    return root
