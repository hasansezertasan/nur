"""Static facts about Python code that nox discovery relies on.

Everything here reads a syntax tree without running it: constant values and
truth, which statements always raise or leave a loop, which names an
expression may bind, and which names are certainly exception classes.
"""

from __future__ import annotations

import ast
import builtins
from typing import NamedTuple

__all__ = [
    "NO_VALUE",
    "always_enters",
    "always_true",
    "bound_anywhere",
    "breaks_in",
    "catches",
    "constant_truth",
    "exception_classes",
    "invalid_handler_type",
    "irrefutable",
    "literal_match",
    "never_enters",
    "raised_name",
    "signed_number",
    "stored_names",
    "terminates",
]


def raised_name(node: ast.Raise) -> str | None:
    """Return the class name an explicit ``raise X`` / ``raise X(...)`` raises.

    A literal exception or cause (``raise 1``, ``raise E from 1``; ``from None``
    is fine) makes Python raise ``TypeError`` instead. None if unknown, or if
    evaluating the ``raise`` rebinds that very name (``raise X from (X := ...)``),
    since a handler naming ``X`` then looks up the new value.
    """
    cause = node.cause
    invalid_cause = (
        cause is not None
        and not (isinstance(cause, ast.Constant) and cause.value is None)
        and _is_literal(cause)
    )
    if (node.exc is not None and _is_literal(node.exc)) or invalid_cause:
        return "TypeError"
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    if not isinstance(exc, ast.Name) or exc.id in stored_names(node):
        return None
    return exc.id


def catches(
    handler: ast.ExceptHandler,
    raised: str | None,
    known_classes: dict[str, _ExceptionClass],
) -> bool:
    """Return True if *handler* certainly catches an exception named *raised*.

    A handler naming a known exception class (see ``exception_classes``)
    catches a known raised class when it is one of that class's ancestors, as
    in ``raise FileNotFoundError`` / ``except OSError``. Unknown names are never
    trusted: ``except`` rejects instances such as ``X = TypeError()``, and a
    rebound ``Exception = ValueError`` changes what it catches. Only a bare
    ``except:`` or an unshadowed ``BaseException`` catches an unknown raise.
    """
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = {t.id for t in types if isinstance(t, ast.Name)}
    trusted = {name for name in names if name in known_classes}
    if "BaseException" in trusted:
        return True
    known = known_classes.get(raised) if raised is not None else None
    return known is not None and bool(trusted & known.ancestors)


_BUILTIN_EXCEPTIONS = frozenset(
    name
    for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, BaseException)
)


class _ExceptionClass(NamedTuple):
    """A name known to be bound to an exception class."""

    line: int  # First line where the name is bound (0: builtin, always bound).
    ancestors: frozenset[str]  # Known class names it inherits from, itself included.


def _builtin_ancestors(name: str, trusted: set[str]) -> frozenset[str]:
    cls = getattr(builtins, name)
    return frozenset(base.__name__ for base in cls.__mro__ if base.__name__ in trusted)


def exception_classes(
    tree: ast.Module, rebound: set[str]
) -> dict[str, _ExceptionClass]:
    """Map names certainly bound to exception classes to where and what they are.

    That is builtin exceptions the file never rebinds, plus names bound only by
    plain top-level ``class`` statements (no decorators or class keywords such
    as ``metaclass=``, either of which can bind the name to anything) whose
    every definition has a base that is itself such a class. A user class is
    unbound before its first definition, and its known ancestors are those
    every definition shares.
    """
    top_level = {id(node) for node in tree.body}
    classes: dict[str, list[ast.ClassDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes.setdefault(node.name, []).append(node)
    other = bound_anywhere(tree, include_classes=False)
    candidates = {
        name: defs
        for name, defs in classes.items()
        if name not in other
        and all(
            id(d) in top_level and not d.decorator_list and not d.keywords for d in defs
        )
    }
    unshadowed = set(_BUILTIN_EXCEPTIONS - rebound)
    known = {
        name: _ExceptionClass(0, _builtin_ancestors(name, unshadowed))
        for name in unshadowed
    }
    changed = True
    while changed:
        changed = False
        for name, defs in candidates.items():
            if name in known:
                continue
            per_def = [
                [
                    known[b.id]
                    for b in d.bases
                    if isinstance(b, ast.Name) and b.id in known
                ]
                for d in defs
            ]
            if all(per_def):
                shared: frozenset[str] = frozenset.intersection(
                    *(
                        frozenset().union(*(base.ancestors for base in bases))
                        for bases in per_def
                    )
                )
                line = min(d.lineno for d in defs)
                known[name] = _ExceptionClass(line, shared | {name})
                changed = True
    return known


def bound_anywhere(tree: ast.Module, *, include_classes: bool = True) -> set[str]:
    """Return every name the file binds anywhere, in any scope.

    With ``include_classes=False``, names bound by a ``class`` statement are
    left out (unless something else also binds them).
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            # A class decorator may bind the name to anything, even an instance.
            if include_classes or node.decorator_list:
                names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.partition(".")[0])
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def terminates(node: ast.stmt) -> bool:
    """Return True for a statement that always raises: ``raise``, ``assert False``.

    nox imports noxfiles without ``-O``, so assertions are live.
    """
    if isinstance(node, ast.Raise):
        return True
    return isinstance(node, ast.Assert) and constant_truth(node.test) is False


def irrefutable(pattern: ast.pattern) -> bool:
    """Return True for a pattern that always matches (``_``, ``x``, ``_ as y``)."""
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(irrefutable(alternative) for alternative in pattern.patterns)
    return False


# Marks "not a statically known value" (None is itself a literal).
NO_VALUE = object()


def literal_match(pattern: ast.pattern, subject: object) -> bool | None:
    """Return whether a literal pattern matches a constant *subject*.

    ``case 1:`` compares with ``==`` and ``case None:``/``case True:`` with
    ``is``, as Python does. None means the pattern is not a literal.
    """
    if isinstance(pattern, ast.MatchValue):
        value = signed_number(pattern.value)
        if value is not NO_VALUE:
            return bool(value == subject)
    if isinstance(pattern, ast.MatchSingleton):
        return pattern.value is subject
    if isinstance(pattern, ast.MatchOr):
        results = [literal_match(alt, subject) for alt in pattern.patterns]
        if True in results:
            return True
        if all(result is False for result in results):
            return False
    return None


_LITERALS = (
    ast.Constant
    | ast.List
    | ast.Set
    | ast.Dict
    | ast.JoinedStr
    | ast.ListComp
    | ast.SetComp
    | ast.DictComp
    | ast.GeneratorExp
    | ast.Lambda
)


def _is_literal(expr: ast.expr) -> bool:
    """Return True for an expression that can never be an exception class.

    Literals, and ``-1`` / ``1 + 1`` / ``0 or 1`` built only from them.
    """
    if isinstance(expr, ast.Tuple):
        return any(_is_literal(elt) for elt in expr.elts)
    if isinstance(expr, ast.UnaryOp):
        return _is_literal(expr.operand)
    if isinstance(expr, ast.BinOp):
        return _is_literal(expr.left) and _is_literal(expr.right)
    if isinstance(expr, ast.BoolOp):
        return all(_is_literal(value) for value in expr.values)
    return isinstance(expr, _LITERALS)


def signed_number(expr: ast.expr) -> object:
    """Return the value of ``1``, ``-1`` or ``+1.5``, else ``NO_VALUE``."""
    if isinstance(expr, ast.Constant):
        return expr.value
    if (
        isinstance(expr, ast.UnaryOp)
        and isinstance(expr.op, ast.USub | ast.UAdd)
        and isinstance(expr.operand, ast.Constant)
        and isinstance(expr.operand.value, int | float | complex)
    ):
        value = expr.operand.value
        return -value if isinstance(expr.op, ast.USub) else value
    return NO_VALUE


def invalid_handler_type(handler: ast.ExceptHandler) -> bool:
    """Return True for ``except 1:`` / ``except []:``, which raise TypeError."""
    return handler.type is not None and _is_literal(handler.type)


def constant_truth(expr: ast.expr | None) -> bool | None:
    """Return the truth of a statically known expression, or None if unknown.

    Covers literals, ``not``, unary ``+``/``-``/``~`` on numbers, and container
    literals without ``*`` unpacking (whose emptiness is then known).
    """
    if isinstance(expr, ast.Constant):
        return bool(expr.value)
    if isinstance(expr, ast.UnaryOp):
        return _unary_truth(expr)
    length = None if expr is None else _literal_length(expr)
    return None if length is None else length > 0


def _unary_truth(expr: ast.UnaryOp) -> bool | None:
    if isinstance(expr.op, ast.Not):
        inner = constant_truth(expr.operand)
        return None if inner is None else not inner
    operand = expr.operand
    if not isinstance(operand, ast.Constant):
        return None
    value = operand.value
    numeric = isinstance(value, int | float | complex)
    if isinstance(expr.op, ast.USub | ast.UAdd) and numeric:
        return bool(value)
    # `~` on a bool is deprecated, so only plain ints are evaluated.
    if (
        isinstance(expr.op, ast.Invert)
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return bool(~value)
    return None


def never_enters(node: ast.For | ast.AsyncFor | ast.While) -> bool:
    """Return True for loops whose body cannot run: ``while False``, ``for x in []``."""
    if isinstance(node, ast.While):
        return constant_truth(node.test) is False
    return _literal_length(node.iter) == 0


def always_enters(node: ast.For | ast.AsyncFor) -> bool:
    """Return True for a ``for`` over a literal known to be non-empty."""
    length = _literal_length(node.iter)
    return length is not None and length > 0


def _literal_length(expr: ast.expr) -> int | None:
    """Return the length of a literal iterable, or None if it isn't known."""
    if isinstance(expr, ast.List | ast.Tuple | ast.Set):
        if any(isinstance(elt, ast.Starred) for elt in expr.elts):
            return None
        return len(expr.elts)
    if isinstance(expr, ast.Dict):
        return None if None in expr.keys else len(expr.keys)
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str | bytes):
        return len(expr.value)
    return None


def always_true(test: ast.expr) -> bool:
    return constant_truth(test) is True


def _always_diverts(body: list[ast.stmt]) -> bool:
    """Return True if *body* always ends in ``raise``/``assert False``/``continue``.

    Follows constant ``if`` branches and ``if``/``else`` pairs that both divert.
    """
    for node in body:
        if terminates(node) or isinstance(node, ast.Continue):
            return True
        if isinstance(node, ast.If):
            truth = constant_truth(node.test)
            if truth is not None:
                if _always_diverts(node.body if truth else node.orelse):
                    return True
            elif _always_diverts(node.body) and _always_diverts(node.orelse):
                return True
    return False


def breaks_in(body: list[ast.stmt], *, or_continues: bool = False) -> bool:
    """Return True if *body* contains a reachable ``break`` (or ``continue``)."""
    exits = ast.Break | ast.Continue if or_continues else ast.Break
    for node in body:
        if isinstance(node, exits):
            return True
        if isinstance(node, ast.Continue) or terminates(node):
            # A `continue` or unconditional raise skips the rest of this block.
            return False
        if _breaks_in_statement(node, or_continues=or_continues):
            return True
        if _always_diverts([node]):
            return False  # e.g. `if True: raise ...` skips the rest of this block.
    return False


def _breaks_in_statement(node: ast.stmt, *, or_continues: bool) -> bool:
    """Return True if a compound statement can reach a ``break`` for our loop."""
    if isinstance(node, ast.Try | ast.TryStar) and _always_diverts(node.finalbody):
        # A `finally` that always raises or `continue`s overrides any `break`
        # in the `try`, so only a `break` in the `finally` itself can escape.
        return breaks_in(node.finalbody, or_continues=or_continues)
    truth = constant_truth(node.test) if isinstance(node, ast.If) else None
    if isinstance(node, ast.If) and truth is not None:
        # Only the branch a constant condition takes can reach its `break`.
        taken = node.body if truth else node.orelse
        return breaks_in(taken, or_continues=or_continues)
    # A nested loop's `break` and a function's or class's body belong elsewhere.
    scopes = ast.For | ast.AsyncFor | ast.While | ast.FunctionDef | ast.AsyncFunctionDef
    if isinstance(node, scopes | ast.ClassDef):
        return False
    blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
    blocks += [handler.body for handler in getattr(node, "handlers", [])]
    if isinstance(node, ast.Match):
        blocks += [case.body for case in _reachable_cases(node)]
    return any(breaks_in(block, or_continues=or_continues) for block in blocks)


def _reachable_cases(node: ast.Match) -> list[ast.match_case]:
    """Return the cases whose body may run, as ``_Scanner._match`` decides it.

    A literal differing from a constant subject or a constant-false guard never
    runs its body, and nothing after a certain match is reached.
    """
    subject = signed_number(node.subject)
    reachable: list[ast.match_case] = []
    for case in node.cases:
        literal = None if subject is NO_VALUE else literal_match(case.pattern, subject)
        guard = constant_truth(case.guard)
        if literal is False or guard is False:
            continue
        reachable.append(case)
        certain = irrefutable(case.pattern) or literal is True
        if certain and (case.guard is None or guard is True):
            break
    return reachable


_COMPREHENSIONS = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp


def stored_names(*nodes: ast.AST | None) -> set[str]:
    """Return every name *nodes* may bind or delete in the enclosing scope.

    Covers assignment, walrus, ``as`` and pattern captures. Lambda bodies and
    comprehension loop variables are local to their own scope and skipped, but
    a walrus inside a comprehension binds the enclosing scope and counts.
    """
    names: set[str] = set()
    for node in nodes:
        if node is not None:
            _collect_stores(node, names, in_comprehension=False)
    return names


def _lambda_defaults(node: ast.Lambda) -> list[ast.expr]:
    defaults = [*node.args.defaults, *node.args.kw_defaults]
    return [default for default in defaults if default is not None]


def _collect_stores(node: ast.AST, names: set[str], *, in_comprehension: bool) -> None:
    if isinstance(node, ast.Lambda):
        # Defaults are evaluated here; parameters and body are the lambda's own.
        for default in _lambda_defaults(node):
            _collect_stores(default, names, in_comprehension=in_comprehension)
        return
    if isinstance(node, ast.AnnAssign) and node.value is None:
        return  # `nox: object` annotates without binding (annotations are lazy).
    if isinstance(node, ast.NamedExpr):
        names.add(node.target.id)
    elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
        if not in_comprehension:
            names.add(node.id)
    elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
        names.add(node.name)
    elif isinstance(node, ast.MatchMapping) and node.rest:
        names.add(node.rest)
    nested = in_comprehension or isinstance(node, _COMPREHENSIONS)
    for child in ast.iter_child_nodes(node):
        _collect_stores(child, names, in_comprehension=nested)
