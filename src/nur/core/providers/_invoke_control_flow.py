"""Literal loop and condition analysis for static Invoke discovery."""

from __future__ import annotations

import ast

__all__ = ["constant_truth", "loop_count", "loop_must_enter"]


def constant_truth(expression: ast.expr) -> bool | None:
    try:
        return bool(ast.literal_eval(expression))
    except (ValueError, TypeError) as _exc:
        return None


def loop_count(statement: ast.For | ast.AsyncFor | ast.While) -> int | None:
    if any(
        node is not statement
        and isinstance(
            node, (ast.For, ast.AsyncFor, ast.While, ast.Break, ast.Continue)
        )
        for node in ast.walk(statement)
    ):
        return None
    if isinstance(statement, ast.While):
        return (
            0
            if isinstance(statement.test, ast.Constant) and not statement.test.value
            else None
        )
    if isinstance(statement.iter, (ast.List, ast.Tuple)) and not any(
        isinstance(item, ast.Starred) for item in statement.iter.elts
    ):
        return len(statement.iter.elts)
    return None


def loop_must_enter(statement: ast.For | ast.AsyncFor | ast.While) -> bool:
    if isinstance(statement, ast.While):
        return constant_truth(statement.test) is True
    if isinstance(statement.iter, (ast.List, ast.Tuple, ast.Set)):
        return bool(statement.iter.elts) and not any(
            isinstance(item, ast.Starred) for item in statement.iter.elts
        )
    if isinstance(statement.iter, ast.Dict):
        return any(key is not None for key in statement.iter.keys)
    if isinstance(statement.iter, ast.Constant) and isinstance(
        statement.iter.value, (str, bytes)
    ):
        return bool(statement.iter.value)
    return False
