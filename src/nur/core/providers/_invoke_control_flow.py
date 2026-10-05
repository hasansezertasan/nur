"""Pure Python syntax analysis for static Invoke discovery."""

from __future__ import annotations

import ast

__all__ = [
    "MAX_UNROLLED_ITERATIONS",
    "all_statement_blocks",
    "constant_truth",
    "definitely_executed_blocks",
    "exception_taints",
    "excludes_exception",
    "global_names",
    "guaranteed_match_case",
    "iteration_jump",
    "loop_count",
    "loop_must_enter",
    "loop_task_blocks",
    "nonraising_block",
    "unpacked_pairs",
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


_EXCEPTIONS: dict[str, type[BaseException]] = {
    cls.__name__: cls
    for cls in (
        TypeError,
        AttributeError,
        ValueError,
        KeyError,
        IndexError,
        LookupError,
        RuntimeError,
        OSError,
        AssertionError,
        SyntaxError,
        ImportError,
        Exception,
        BaseException,
    )
}


def excludes_exception(
    expression: ast.expr | None,
    exception: str,
    bindings: dict[str, str],
    tainted: set[str],
) -> bool:
    if isinstance(expression, ast.Tuple):
        return all(
            excludes_exception(item, exception, bindings, tainted)
            for item in expression.elts
        )
    if (
        not isinstance(expression, ast.Name)
        or expression.id not in _EXCEPTIONS
        or expression.id in bindings
        or "builtins." + expression.id in tainted
    ):
        return False
    return not issubclass(_EXCEPTIONS[exception], _EXCEPTIONS[expression.id])


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
        "builtins." + name for name in _EXCEPTIONS if name in written or "*" in written
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


MAX_UNROLLED_ITERATIONS = 2


def loop_task_blocks(
    statement: ast.For | ast.AsyncFor | ast.While,
) -> list[list[ast.stmt]]:
    count = loop_count(statement)
    if count is not None and count <= MAX_UNROLLED_ITERATIONS:
        body = statement.body
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            # Every iteration overwrites the target, even if the body assigns a
            # Task to that name. Its next runtime value is not a known Task.
            body = [
                ast.Assign(targets=[statement.target], value=ast.Constant(None)),
                *body,
            ]
        return [body * count + statement.orelse]
    # Inspect possible copies for default collisions, but survival of written
    # task bindings is handled conservatively for arbitrary iteration counts.
    return [statement.body + statement.orelse, statement.orelse]


def definitely_executed_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        return [] if truth is None else [statement.body if truth else statement.orelse]
    if isinstance(statement, ast.ClassDef):
        return [statement.body]
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        count = loop_count(statement)
        if count is not None and count <= MAX_UNROLLED_ITERATIONS:
            return loop_task_blocks(statement)
        return [statement.body] if loop_must_enter(statement) else []
    if isinstance(statement, ast.Match):
        case = guaranteed_match_case(statement)
        return [case.body] if case is not None else []
    return []


def _pattern_matches(pattern: ast.pattern, value: object) -> bool | None:
    if isinstance(pattern, ast.MatchAs):
        return (
            True
            if pattern.pattern is None
            else _pattern_matches(pattern.pattern, value)
        )
    if isinstance(pattern, ast.MatchSingleton):
        return value is pattern.value
    if isinstance(pattern, ast.MatchValue):
        try:
            return bool(value == ast.literal_eval(pattern.value))
        except (ValueError, TypeError) as _exc:
            return None
    if isinstance(pattern, ast.MatchOr):
        matches = [_pattern_matches(child, value) for child in pattern.patterns]
        return True if True in matches else (None if None in matches else False)
    return None


def guaranteed_match_case(statement: ast.Match) -> ast.match_case | None:
    try:
        value = ast.literal_eval(statement.subject)
    except (ValueError, TypeError) as _exc:
        if len(statement.cases) != 1:
            return None
        value = object()
    for case in statement.cases:
        matches = _pattern_matches(case.pattern, value)
        guard = True if case.guard is None else constant_truth(case.guard)
        if matches is False or guard is False:
            continue
        return case if matches is True and guard is True else None
    return None


def unpacked_pairs(
    target: ast.Tuple | ast.List, value: ast.Tuple | ast.List
) -> list[tuple[ast.expr, ast.expr]]:
    if any(isinstance(item, ast.Starred) for item in value.elts):
        return []
    starred = next(
        (
            index
            for index, item in enumerate(target.elts)
            if isinstance(item, ast.Starred)
        ),
        None,
    )
    if starred is None:
        return (
            list(zip(target.elts, value.elts, strict=True))
            if len(target.elts) == len(value.elts)
            else []
        )
    if len(value.elts) < len(target.elts) - 1:
        return []
    suffix = len(target.elts) - starred - 1
    pairs = list(zip(target.elts[:starred], value.elts[:starred], strict=True))
    if suffix:
        pairs.extend(zip(target.elts[-suffix:], value.elts[-suffix:], strict=True))
    return pairs


def all_statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    """Get compound blocks without crossing function or class scopes."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    blocks: list[list[ast.stmt]] = []
    for _, value in ast.iter_fields(statement):
        if isinstance(value, list) and value:
            if all(isinstance(item, ast.stmt) for item in value):
                blocks.append(value)
            elif all(
                isinstance(item, (ast.ExceptHandler, ast.match_case)) for item in value
            ):
                blocks.extend(item.body for item in value)
    return blocks


def global_names(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, ast.Global):
            names.update(statement.names)
        for block in all_statement_blocks(statement):
            names.update(global_names(block))
    return names
