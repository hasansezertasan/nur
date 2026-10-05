"""Pure Python syntax analysis for static Invoke discovery."""

from __future__ import annotations

import ast
import operator
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Container

__all__ = [
    "MAX_UNROLLED_ITERATIONS",
    "all_statement_blocks",
    "compound_children",
    "constant_truth",
    "definitely_executed_blocks",
    "exception_taints",
    "excludes_exception",
    "global_names",
    "guaranteed_match_case",
    "iteration_jump",
    "iteration_prefix",
    "loop_count",
    "loop_else_separately",
    "loop_exhausts",
    "loop_must_enter",
    "loop_task_blocks",
    "nonraising_block",
    "reachable_match_cases",
    "statement_blocks",
    "statement_terminates",
    "stringify_future_annotations",
    "try_outcome_blocks",
    "unpacked_pairs",
]


_UNKNOWN = object()


def _literal_contains(left: object, right: object) -> bool:
    container: Container[object] = cast("Container[object]", right)
    return operator.contains(container, left)


_COMPARE_OPERATORS: dict[type[ast.cmpop], Callable[[object, object], object]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: cast("Callable[[object, object], object]", operator.lt),
    ast.LtE: cast("Callable[[object, object], object]", operator.le),
    ast.Gt: cast("Callable[[object, object], object]", operator.gt),
    ast.GtE: cast("Callable[[object, object], object]", operator.ge),
    ast.In: _literal_contains,
    ast.NotIn: lambda left, right: not _literal_contains(left, right),
}


def constant_truth(expression: ast.expr) -> bool | None:
    value = _constant_value(expression)
    return None if value is _UNKNOWN else bool(value)


def _constant_value(expression: ast.expr) -> object:
    if isinstance(expression, ast.Call):
        return _UNKNOWN
    try:
        value: object = ast.literal_eval(expression)
    except (ValueError, TypeError) as _exc:
        return _boolean_value(expression)
    return value


def _boolean_value(expression: ast.expr) -> object:
    if isinstance(expression, ast.UnaryOp) and isinstance(expression.op, ast.Not):
        value = _constant_value(expression.operand)
        return _UNKNOWN if value is _UNKNOWN else not value
    if isinstance(expression, ast.BoolOp):
        value = _UNKNOWN
        for child in expression.values:
            value = _constant_value(child)
            if value is _UNKNOWN or bool(value) == isinstance(expression.op, ast.Or):
                break
        return value
    if isinstance(expression, ast.Compare):
        return _comparison_value(expression)
    return _UNKNOWN


def _compare_literals(operation: ast.cmpop, left: object, right: object) -> object:
    if isinstance(operation, (ast.Is, ast.IsNot)):
        # Identity of non-singleton literals depends on Python's constant pool.
        if any(
            value is not None and not isinstance(value, bool) for value in (left, right)
        ):
            return _UNKNOWN
        return (left is right) == isinstance(operation, ast.Is)
    compare = _COMPARE_OPERATORS[type(operation)]
    try:
        return bool(compare(left, right))
    except (ValueError, TypeError) as _exc:
        return _UNKNOWN


def _comparison_value(expression: ast.Compare) -> object:
    left = _constant_value(expression.left)
    for operation, comparator in zip(
        expression.ops, expression.comparators, strict=True
    ):
        right = _constant_value(comparator)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        matches = _compare_literals(operation, left, right)
        if matches is _UNKNOWN or not matches:
            return matches
        left = right
    return True


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
        and isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.Break))
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


def loop_exhausts(statement: ast.For | ast.AsyncFor | ast.While) -> bool:
    if isinstance(statement, ast.While):
        return constant_truth(statement.test) is False
    finite = isinstance(statement.iter, (ast.List, ast.Tuple, ast.Set, ast.Dict)) or (
        isinstance(statement.iter, ast.Constant)
        and isinstance(statement.iter.value, (str, bytes))
    )
    return finite and not _loop_breaks(statement.body)


def _loop_breaks(statements: list[ast.stmt]) -> bool:
    pending: list[ast.AST] = list(statements)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Break):
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            # A break in a nested loop body exits that loop. Its else suite,
            # however, executes outside the nested loop and can exit ours.
            pending.extend(node.orelse)
        elif isinstance(node, ast.If) and constant_truth(node.test) is not None:
            pending.extend(node.body if constant_truth(node.test) else node.orelse)
        else:
            pending.extend(ast.iter_child_nodes(node))
    return False


def loop_must_enter(statement: ast.For | ast.AsyncFor | ast.While) -> bool:
    if isinstance(statement, ast.While):
        return constant_truth(statement.test) is True
    if isinstance(statement.iter, (ast.List, ast.Tuple, ast.Set)):
        return any(not isinstance(item, ast.Starred) for item in statement.iter.elts)
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
        ZeroDivisionError,
        ArithmeticError,
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
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return _guaranteed_jump(statement)
    return False


def _block_jump(statements: list[ast.stmt]) -> bool:
    return any(_guaranteed_jump(statement) for statement in statements)


def _guaranteed_jump(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        if truth is not None:
            return _block_jump(statement.body if truth else statement.orelse)
        return _block_jump(statement.body) and _block_jump(statement.orelse)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        if _block_jump(statement.finalbody):
            return True
        prefix = iteration_prefix(statement.body)
        return _block_jump(prefix) and (
            not statement.handlers
            or nonraising_block(prefix[:-1])
            or all(_block_jump(handler.body) for handler in statement.handlers)
        )
    return False


def iteration_prefix(
    statements: list[ast.stmt],
    bindings: dict[str, str] | None = None,
    tainted: set[str] | None = None,
) -> list[ast.stmt]:
    """Retain statements up to a guaranteed exit from this block."""
    for index, statement in enumerate(statements):
        if statement_terminates(statement, bindings, tainted):
            return statements[: index + 1]
    return statements


def statement_terminates(
    statement: ast.stmt,
    bindings: dict[str, str] | None = None,
    tainted: set[str] | None = None,
) -> bool:
    if isinstance(statement, ast.Raise) or _guaranteed_jump(statement):
        return True
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        blocks = (
            [statement.body, statement.orelse]
            if truth is None
            else [statement.body if truth else statement.orelse]
        )
        return all(
            any(statement_terminates(child, bindings, tainted) for child in block)
            for block in blocks
        )
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return _try_terminates(statement, bindings, tainted)
    return False


def _raised_exception(
    body: list[ast.stmt], bindings: dict[str, str], tainted: set[str]
) -> str | None:
    prefix = iteration_prefix(body, bindings, tainted)
    if not prefix or not isinstance(prefix[-1], ast.Raise):
        return None
    exception = prefix[-1].exc
    if isinstance(exception, ast.Call):
        if (
            any(_constant_value(arg) is _UNKNOWN for arg in exception.args)
            or exception.keywords
        ):
            return None
        exception = exception.func
    if (
        not isinstance(exception, ast.Name)
        or exception.id in bindings
        or "builtins." + exception.id in tainted
    ):
        return None
    return exception.id if exception.id in _EXCEPTIONS else None


def _try_terminates(
    statement: ast.Try | ast.TryStar,
    bindings: dict[str, str] | None,
    tainted: set[str] | None,
) -> bool:
    if any(
        statement_terminates(child, bindings, tainted) for child in statement.finalbody
    ):
        return True
    if any(statement_terminates(child, bindings, tainted) for child in statement.body):
        exception = (
            None
            if bindings is None
            else _raised_exception(statement.body, bindings, tainted or set())
        )
        return all(
            (
                exception is not None
                and excludes_exception(
                    handler.type, exception, bindings or {}, tainted or set()
                )
            )
            or any(
                statement_terminates(child, bindings, tainted) for child in handler.body
            )
            for handler in statement.handlers
        )
    return nonraising_block(statement.body) and any(
        statement_terminates(child, bindings, tainted) for child in statement.orelse
    )


def stringify_future_annotations(tree: ast.Module) -> None:
    if not any(
        isinstance(statement, ast.ImportFrom)
        and statement.module == "__future__"
        and any(alias.name == "annotations" for alias in statement.names)
        for statement in tree.body
    ):
        return
    # Future annotations are strings; inspect.signature does not execute them.
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            node.annotation = ast.Constant(value="")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.returns is not None
        ):
            node.returns = ast.Constant(value="")


def try_outcome_blocks(statement: ast.Try | ast.TryStar) -> list[list[ast.stmt]]:
    body = iteration_prefix(statement.body)
    else_body = [] if body and statement_terminates(body[-1]) else statement.orelse
    return [
        body + iteration_prefix(else_body),
        *(iteration_prefix(handler.body) for handler in statement.handlers),
    ]


def exception_taints(written: set[str]) -> set[str]:
    return {
        "builtins." + name for name in _EXCEPTIONS if name in written or "*" in written
    }


def _nonraising_statement(statement: ast.stmt) -> bool:
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr):
        return _constant_value(statement.value) is not _UNKNOWN
    return (
        isinstance(statement, ast.Assign)
        and all(isinstance(target, ast.Name) for target in statement.targets)
        and _constant_value(statement.value) is not _UNKNOWN
    )


def nonraising_block(statements: list[ast.stmt]) -> bool:
    return all(_nonraising_statement(statement) for statement in statements)


MAX_UNROLLED_ITERATIONS = 2


def loop_else_separately(statement: ast.stmt) -> bool:
    if not isinstance(statement, (ast.For, ast.AsyncFor)):
        return False
    count = loop_count(statement)
    if count is None:
        return loop_exhausts(statement)
    return count > MAX_UNROLLED_ITERATIONS or (
        count > 0
        and any(isinstance(node, ast.Continue) for node in ast.walk(statement))
    )


def loop_task_blocks(
    statement: ast.For | ast.AsyncFor | ast.While,
) -> list[list[ast.stmt]]:
    count = loop_count(statement)
    if count is not None and count <= MAX_UNROLLED_ITERATIONS:
        body = iteration_prefix(statement.body)
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
    return [iteration_prefix(statement.body) + statement.orelse, statement.orelse]


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


def reachable_match_cases(statement: ast.Match) -> list[ast.match_case]:
    try:
        value = ast.literal_eval(statement.subject)
    except (ValueError, TypeError) as _exc:
        value = _UNKNOWN
    cases: list[ast.match_case] = []
    for case in statement.cases:
        matches = (
            None
            if value is _UNKNOWN
            and not (
                isinstance(case.pattern, ast.MatchAs) and case.pattern.pattern is None
            )
            else _pattern_matches(case.pattern, value)
        )
        if matches is False:
            continue
        guard = True if case.guard is None else constant_truth(case.guard)
        cases.append(
            ast.match_case(pattern=case.pattern, guard=case.guard, body=[])
            if guard is False
            else case
        )
        if matches is True and guard is True:
            break
    return cases


def guaranteed_match_case(statement: ast.Match) -> ast.match_case | None:
    for case in reachable_match_cases(statement):
        guard = True if case.guard is None else constant_truth(case.guard)
        if guard is False:
            continue
        try:
            value = ast.literal_eval(statement.subject)
        except (ValueError, TypeError) as _exc:
            value = _UNKNOWN
        matches = (
            _pattern_matches(case.pattern, value)
            if value is not _UNKNOWN
            else (
                True
                if isinstance(case.pattern, ast.MatchAs)
                and case.pattern.pattern is None
                else None
            )
        )
        return case if matches is True and guard is True else None
    return None


def _exception_children(
    node: ast.Try | ast.TryStar | ast.ExceptHandler,
) -> list[ast.AST]:
    if isinstance(node, (ast.Try, ast.TryStar)):
        body = iteration_prefix(node.body)
        else_body = [] if body and statement_terminates(body[-1]) else node.orelse
        return [
            *body,
            *node.handlers,
            *iteration_prefix(else_body),
            *iteration_prefix(node.finalbody),
        ]
    return [
        *([node.type] if node.type is not None else []),
        *iteration_prefix(node.body),
    ]


def compound_children(node: ast.AST) -> list[ast.AST] | None:
    if isinstance(node, (ast.Try, ast.TryStar, ast.ExceptHandler)):
        return _exception_children(node)
    if isinstance(node, ast.Match):
        return [node.subject, *reachable_match_cases(node)]
    if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
        expression = node.test if isinstance(node, ast.While) else node.iter
        if loop_count(node) == 0:
            return [expression, *node.orelse]
        target = [] if isinstance(node, ast.While) else [node.target]
        return [expression, *target, *iteration_prefix(node.body), *node.orelse]
    if isinstance(node, ast.If):
        truth = constant_truth(node.test)
        bodies = (
            [node.body, node.orelse]
            if truth is None
            else [node.body if truth else node.orelse]
        )
        return [
            node.test,
            *(child for body in bodies for child in iteration_prefix(body)),
        ]
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


def statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if (
        isinstance(statement, (ast.For, ast.AsyncFor, ast.While))
        and loop_count(statement) == 0
    ):
        return [statement.orelse]
    if isinstance(statement, ast.If):
        truth = constant_truth(statement.test)
        if truth is not None:
            return [iteration_prefix(statement.body if truth else statement.orelse)]
    if isinstance(statement, ast.Match):
        return [case.body for case in reachable_match_cases(statement)]
    return [iteration_prefix(block) for block in all_statement_blocks(statement)]
