"""Bounded evaluation of literal Python syntax, without project execution."""

from __future__ import annotations

import ast
import operator
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Container

__all__ = ["UNKNOWN", "compare_literals", "constant_value", "literal_exception"]

UNKNOWN = object()
_MAX_MAGNITUDE = 100
_MAX_SEQUENCE = 1024
_MAX_INTEGER_BITS = 4096


def _contains(left: object, right: object) -> bool:
    container: Container[object] = cast("Container[object]", right)
    return operator.contains(container, left)


def _not_contains(left: object, right: object) -> bool:
    return not _contains(left, right)


_BINARY: dict[type[ast.operator], Callable[..., object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.MatMult: operator.matmul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
    ast.BitOr: operator.or_,
    ast.BitAnd: operator.and_,
    ast.BitXor: operator.xor,
}
_UNARY: dict[type[ast.unaryop], Callable[..., object]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Invert: operator.invert,
    ast.Not: operator.not_,
}
_COMPARE: dict[type[ast.cmpop], Callable[..., object]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: _contains,
    ast.NotIn: _not_contains,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}


def _apply(
    operation: Callable[..., object], *values: object
) -> tuple[object, str | None]:
    try:
        return operation(*values), None
    except (
        TypeError,
        ValueError,
        ZeroDivisionError,
        KeyError,
        IndexError,
        OverflowError,
    ) as exception:
        return UNKNOWN, type(exception).__name__


def constant_value(expression: ast.expr) -> object:
    value, exception = _result(expression)
    return UNKNOWN if exception is not None else value


def literal_exception(expression: ast.expr) -> str | None:
    return _result(expression)[1]


def _result(expression: ast.expr) -> tuple[object, str | None]:
    if isinstance(expression, ast.Constant):
        return expression.value, None
    if isinstance(expression, (ast.BinOp, ast.UnaryOp, ast.Subscript)):
        return _arithmetic(expression)
    if isinstance(expression, (ast.BoolOp, ast.Compare, ast.IfExp)):
        return _logical(expression)
    if isinstance(expression, ast.NamedExpr):
        return _result(expression.value)
    if isinstance(expression, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
        return _container(expression)
    return UNKNOWN, None


def _bounded(operation: ast.operator, left: object, right: object) -> bool:
    if any(
        isinstance(value, int) and value.bit_length() > _MAX_INTEGER_BITS
        for value in (left, right)
    ):
        return False
    if isinstance(operation, ast.Mod) and isinstance(left, (str, bytes)):
        return False
    if any(
        isinstance(value, (str, bytes, list, tuple, dict, set))
        and len(value) > _MAX_SEQUENCE
        for value in (left, right)
    ):
        return False
    if isinstance(operation, (ast.Pow, ast.LShift, ast.RShift)) and isinstance(
        right, int
    ):
        return right <= _MAX_MAGNITUDE
    if isinstance(operation, ast.Mult):
        return not any(
            isinstance(value, int) and abs(value) > _MAX_MAGNITUDE
            for value in (left, right)
        )
    return True


def _arithmetic(
    expression: ast.BinOp | ast.UnaryOp | ast.Subscript,
) -> tuple[object, str | None]:
    if isinstance(expression, ast.UnaryOp):
        value, error = _result(expression.operand)
        return (
            (value, error)
            if value is UNKNOWN
            else _apply(_UNARY[type(expression.op)], value)
        )
    left_expression = (
        expression.left if isinstance(expression, ast.BinOp) else expression.value
    )
    left, error = _result(left_expression)
    if left is UNKNOWN:
        return left, error
    operation: Callable[..., object]
    if isinstance(expression, ast.Subscript):
        right, error = _slice_result(expression.slice)
        operation = operator.getitem
    else:
        right, error = _result(expression.right)
        operation = _BINARY[type(expression.op)]
    if right is UNKNOWN:
        return right, error
    if isinstance(expression, ast.BinOp) and not _bounded(expression.op, left, right):
        return UNKNOWN, None
    return _apply(operation, left, right)


def _slice_result(expression: ast.expr) -> tuple[object, str | None]:
    if not isinstance(expression, ast.Slice):
        return _result(expression)
    parts: list[object] = []
    for part in (expression.lower, expression.upper, expression.step):
        value, error = (None, None) if part is None else _result(part)
        if value is UNKNOWN:
            return value, error
        parts.append(value)
    return _apply(slice, *parts)


def _identity_known(operation: ast.cmpop, left: object, right: object) -> bool:
    return not isinstance(operation, (ast.Is, ast.IsNot)) or all(
        value is None or isinstance(value, bool) for value in (left, right)
    )


def compare_literals(operation: ast.cmpop, left: object, right: object) -> object:
    if not _identity_known(operation, left, right):
        return UNKNOWN
    value, error = _apply(_COMPARE[type(operation)], left, right)
    return UNKNOWN if error else value


def _comparison(expression: ast.Compare) -> tuple[object, str | None]:
    left, error = _result(expression.left)
    if left is UNKNOWN:
        return left, error
    for operation, comparator in zip(
        expression.ops, expression.comparators, strict=True
    ):
        right, error = _result(comparator)
        if right is UNKNOWN:
            return right, error
        if not _identity_known(operation, left, right):
            return UNKNOWN, None
        matches, error = _apply(_COMPARE[type(operation)], left, right)
        if matches is UNKNOWN or not matches:
            return matches, error
        left = right
    return True, None


def _logical(
    expression: ast.BoolOp | ast.Compare | ast.IfExp,
) -> tuple[object, str | None]:
    if isinstance(expression, ast.Compare):
        return _comparison(expression)
    if isinstance(expression, ast.IfExp):
        test, error = _result(expression.test)
        return (
            (test, error)
            if test is UNKNOWN
            else _result(expression.body if test else expression.orelse)
        )
    value: object = UNKNOWN
    error = None
    for child in expression.values:
        value, error = _result(child)
        if value is UNKNOWN or bool(value) == isinstance(expression.op, ast.Or):
            break
    return value, error


def _sequence(expression: ast.List | ast.Tuple | ast.Set) -> tuple[object, str | None]:
    values: list[object] = []
    for child in expression.elts:
        starred = isinstance(child, ast.Starred)
        value, error = _result(child.value if isinstance(child, ast.Starred) else child)
        if value is UNKNOWN:
            return value, error
        if starred:
            expanded, error = _apply(list, value)
            if expanded is UNKNOWN:
                return expanded, error
            values.extend(cast("list[object]", expanded))
        else:
            values.append(value)
    constructor = (
        set
        if isinstance(expression, ast.Set)
        else (tuple if isinstance(expression, ast.Tuple) else list)
    )
    return _apply(constructor, values)


def _container(
    expression: ast.List | ast.Tuple | ast.Set | ast.Dict,
) -> tuple[object, str | None]:
    if not isinstance(expression, ast.Dict):
        return _sequence(expression)
    values: dict[object, object] = {}
    for key, child in zip(expression.keys, expression.values, strict=True):
        key_value, error = (None, None) if key is None else _result(key)
        if key_value is UNKNOWN:
            return key_value, error
        value, error = _result(child)
        if value is UNKNOWN:
            return value, error
        if key is None:
            if not isinstance(value, dict):
                return UNKNOWN, "TypeError"
            values.update(value)
        else:
            _, error = _apply(operator.setitem, values, key_value, value)
            if error:
                return UNKNOWN, error
    return values, None
