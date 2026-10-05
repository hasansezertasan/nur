"""Literal loop and condition analysis for static Invoke discovery."""

from __future__ import annotations

import ast

__all__ = [
    "NON_TYPEERROR_EXCEPTIONS",
    "constant_truth",
    "exception_taints",
    "excludes_typeerror",
    "iteration_jump",
    "loop_count",
    "loop_must_enter",
    "nonraising_block",
]


def constant_truth(expression: ast.expr) -> bool | None:
    try:
        return bool(ast.literal_eval(expression))
    except (ValueError, TypeError) as _exc:
        return None


def loop_count(statement: ast.For | ast.AsyncFor | ast.While) -> int | None:
    if isinstance(statement, ast.While):
        return 0 if constant_truth(statement.test) is False else None
    if isinstance(statement.iter, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        members = (
            statement.iter.keys
            if isinstance(statement.iter, ast.Dict)
            else statement.iter.elts
        )
        if not members:
            return 0
    if any(
        node is not statement
        and isinstance(
            node, (ast.For, ast.AsyncFor, ast.While, ast.Break, ast.Continue)
        )
        for node in ast.walk(statement)
    ):
        return None
    if isinstance(statement.iter, (ast.List, ast.Tuple)) and not any(
        isinstance(item, ast.Starred) for item in statement.iter.elts
    ):
        return len(statement.iter.elts)
    try:
        value = ast.literal_eval(statement.iter)
    except (ValueError, TypeError) as _exc:
        return None
    return len(value) if isinstance(value, (str, bytes, dict, set)) else None


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


NON_TYPEERROR_EXCEPTIONS = frozenset({
    "ValueError",
    "AttributeError",
    "KeyError",
    "IndexError",
    "LookupError",
    "RuntimeError",
    "OSError",
    "AssertionError",
    "SyntaxError",
    "ImportError",
})


def excludes_typeerror(
    expression: ast.expr | None, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if isinstance(expression, ast.Tuple):
        return all(
            excludes_typeerror(item, bindings, tainted) for item in expression.elts
        )
    return (
        isinstance(expression, ast.Name)
        and expression.id in NON_TYPEERROR_EXCEPTIONS
        and expression.id not in bindings
        and "builtins." + expression.id not in tainted
    )


def iteration_jump(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        blocks = (
            [statement.body, statement.orelse]
            if truth is None
            else [statement.body if truth else statement.orelse]
        )
        return any(iteration_jump(child) for block in blocks for child in block)
    return False


def exception_taints(written: set[str]) -> set[str]:
    return {
        "builtins." + name
        for name in NON_TYPEERROR_EXCEPTIONS
        if name in written or "*" in written
    }


def nonraising_block(statements: list[ast.stmt]) -> bool:
    return all(
        isinstance(statement, ast.Pass)
        or (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
        )
        for statement in statements
    )
