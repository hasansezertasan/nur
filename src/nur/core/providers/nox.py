from __future__ import annotations

import ast
import builtins
import enum
import logging
import warnings
from typing import TYPE_CHECKING, NamedTuple

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["NoxProvider", "parse_noxfile"]


log = logging.getLogger("nur")

_SOURCE_FILE = "noxfile.py"
# Modules whose ``session`` is ``nox.session`` or a drop-in wrapper that
# forwards ``name=`` to it (nox-uv).
_NOX_MODULES = frozenset({"nox", "nox_uv"})


class _Kind(enum.Enum):
    """What a tracked name is bound to.

    Bindings map names to a kind; a name missing from the mapping is either
    unbound or bound to something other than nox.
    """

    MODULE = enum.auto()  # a nox module: `<name>.session` is the decorator
    SESSION = enum.auto()  # the `session` decorator itself


def _merge(paths: list[dict[str, _Kind]]) -> dict[str, _Kind]:
    """Keep only the bindings every path agrees on."""
    first, *rest = paths
    return {
        name: kind
        for name, kind in first.items()
        if all(path.get(name) == kind for path in rest)
    }


def _merge_paths(paths: list[dict[str, _Kind] | None]) -> dict[str, _Kind] | None:
    """Merge the paths that continue; None when every path stops (``raise``)."""
    continuing = [path for path in paths if path is not None]
    return _merge(continuing) if continuing else None


def _mutates_session_attr(tree: ast.Module) -> bool:
    """Return True if the file may replace or delete any ``.session`` attribute.

    Aliases share one module object, and a re-import returns the same mutated
    module, so once ``nox.session`` may have been swapped (``nox.session = ...``,
    ``del nox.session``, ``setattr``/``delattr``) no decorator can be trusted.
    Other attributes (``nox.options``, ``nox.needs_version``) do not count.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr == "session" and isinstance(node.ctx, ast.Store | ast.Del):
                return True
        elif _reflective_session_write(node):
            return True
    return False


def _reflective_session_write(node: ast.AST) -> bool:
    """Return True for ``setattr``/``delattr`` calls that may target ``session``."""
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"setattr", "delattr"}
    ):
        return False
    if len(node.args) <= 1:
        return False
    attr = node.args[1]
    # Only a literal naming some other attribute is known to be harmless.
    return not (isinstance(attr, ast.Constant) and attr.value != "session")


def _raised_name(node: ast.Raise) -> str | None:
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
    if not isinstance(exc, ast.Name) or exc.id in _stored_names(node):
        return None
    return exc.id


def _catches(
    handler: ast.ExceptHandler,
    raised: str | None,
    exception_classes: dict[str, _ExceptionClass],
) -> bool:
    """Return True if *handler* certainly catches an exception named *raised*.

    A handler naming a known exception class (see ``_exception_classes``)
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
    trusted = {name for name in names if name in exception_classes}
    if "BaseException" in trusted:
        return True
    known = exception_classes.get(raised) if raised is not None else None
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


def _exception_classes(
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
    other = _bound_anywhere(tree, include_classes=False)
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


def _bound_anywhere(tree: ast.Module, *, include_classes: bool = True) -> set[str]:
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


def _terminates(node: ast.stmt) -> bool:
    """Return True for a statement that always raises: ``raise``, ``assert False``.

    nox imports noxfiles without ``-O``, so assertions are live.
    """
    if isinstance(node, ast.Raise):
        return True
    return isinstance(node, ast.Assert) and _constant_truth(node.test) is False


def _irrefutable(pattern: ast.pattern) -> bool:
    """Return True for a pattern that always matches (``_``, ``x``, ``_ as y``)."""
    if isinstance(pattern, ast.MatchAs):
        return pattern.pattern is None or _irrefutable(pattern.pattern)
    if isinstance(pattern, ast.MatchOr):
        return any(_irrefutable(alternative) for alternative in pattern.patterns)
    return False


# Marks "not a statically known value" (None is itself a literal).
_NO_VALUE = object()


def _literal_match(pattern: ast.pattern, subject: object) -> bool | None:
    """Return whether a literal pattern matches a constant *subject*.

    ``case 1:`` compares with ``==`` and ``case None:``/``case True:`` with
    ``is``, as Python does. None means the pattern is not a literal.
    """
    if isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Constant):
        return bool(pattern.value.value == subject)
    if isinstance(pattern, ast.MatchSingleton):
        return pattern.value is subject
    if isinstance(pattern, ast.MatchOr):
        results = [_literal_match(alt, subject) for alt in pattern.patterns]
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
    """Return True for an expression that can never be an exception class."""
    if isinstance(expr, ast.Tuple):
        return any(_is_literal(elt) for elt in expr.elts)
    return isinstance(expr, _LITERALS)


def _invalid_handler_type(handler: ast.ExceptHandler) -> bool:
    """Return True for ``except 1:`` / ``except []:``, which raise TypeError."""
    return handler.type is not None and _is_literal(handler.type)


def _constant_truth(expr: ast.expr | None) -> bool | None:
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
        inner = _constant_truth(expr.operand)
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


def _never_enters(node: ast.For | ast.AsyncFor | ast.While) -> bool:
    """Return True for loops whose body cannot run: ``while False``, ``for x in []``."""
    if isinstance(node, ast.While):
        return _constant_truth(node.test) is False
    return _literal_length(node.iter) == 0


def _always_enters(node: ast.For | ast.AsyncFor) -> bool:
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


def _always_true(test: ast.expr) -> bool:
    return _constant_truth(test) is True


def _always_diverts(body: list[ast.stmt]) -> bool:
    """Return True if *body* always ends in ``raise``/``assert False``/``continue``.

    Follows constant ``if`` branches and ``if``/``else`` pairs that both divert.
    """
    for node in body:
        if _terminates(node) or isinstance(node, ast.Continue):
            return True
        if isinstance(node, ast.If):
            truth = _constant_truth(node.test)
            if truth is not None:
                if _always_diverts(node.body if truth else node.orelse):
                    return True
            elif _always_diverts(node.body) and _always_diverts(node.orelse):
                return True
    return False


def _breaks_in(body: list[ast.stmt], *, or_continues: bool = False) -> bool:
    """Return True if *body* contains a reachable ``break`` (or ``continue``)."""
    exits = ast.Break | ast.Continue if or_continues else ast.Break
    for node in body:
        if isinstance(node, exits):
            return True
        if isinstance(node, ast.Continue) or _terminates(node):
            # A `continue` or unconditional raise skips the rest of this block.
            return False
        if _breaks_in_statement(node, or_continues=or_continues):
            return True
    return False


def _breaks_in_statement(node: ast.stmt, *, or_continues: bool) -> bool:
    """Return True if a compound statement can reach a ``break`` for our loop."""
    if isinstance(node, ast.Try | ast.TryStar) and _always_diverts(node.finalbody):
        # A `finally` that always raises or `continue`s overrides any `break`
        # in the `try`, so only a `break` in the `finally` itself can escape.
        return _breaks_in(node.finalbody, or_continues=or_continues)
    truth = _constant_truth(node.test) if isinstance(node, ast.If) else None
    if isinstance(node, ast.If) and truth is not None:
        # Only the branch a constant condition takes can reach its `break`.
        taken = node.body if truth else node.orelse
        return _breaks_in(taken, or_continues=or_continues)
    # A nested loop's `break` and a function's or class's body belong elsewhere.
    scopes = ast.For | ast.AsyncFor | ast.While | ast.FunctionDef | ast.AsyncFunctionDef
    if isinstance(node, scopes | ast.ClassDef):
        return False
    blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
    blocks += [handler.body for handler in getattr(node, "handlers", [])]
    blocks += [case.body for case in getattr(node, "cases", [])]
    return any(_breaks_in(block, or_continues=or_continues) for block in blocks)


_COMPREHENSIONS = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp


def _stored_names(*nodes: ast.AST | None) -> set[str]:
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


def _without(bound: dict[str, _Kind], names: set[str]) -> dict[str, _Kind]:
    return {name: kind for name, kind in bound.items() if name not in names}


def _apply_import(
    node: ast.Import | ast.ImportFrom, bound: dict[str, _Kind]
) -> dict[str, _Kind]:
    result = dict(bound)
    if isinstance(node, ast.Import):
        for alias in node.names:
            # `import nox.command` binds the top-level `nox` name too.
            name = alias.asname or alias.name.partition(".")[0]
            target = alias.name if alias.asname else name
            if target in _NOX_MODULES:
                result[name] = _Kind.MODULE
            else:
                result.pop(name, None)
        return result
    from_nox = node.module in _NOX_MODULES and not node.level
    for alias in node.names:
        if alias.name == "*":
            if not from_nox:
                # Any name may be rebound by an unknown star import.
                return {}
            result["session"] = _Kind.SESSION
            continue
        name = alias.asname or alias.name
        if from_nox and alias.name == "session":
            result[name] = _Kind.SESSION
        else:
            result.pop(name, None)
    return result


def _is_session_ref(node: ast.expr, bound: dict[str, _Kind]) -> bool:
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "session"
            and isinstance(node.value, ast.Name)
            and bound.get(node.value.id) is _Kind.MODULE
        )
    return isinstance(node, ast.Name) and bound.get(node.id) is _Kind.SESSION


def _explicit_name(call: ast.Call, default: str) -> str | None:
    """Return the name a ``@nox.session(...)`` call registers, if it is static.

    nox registers ``name or func.__name__``. A ``name=`` that is not a literal,
    a ``**mapping`` that may carry one, or positional arguments make the real
    name unknowable without evaluation.
    """
    if call.args or any(keyword.arg is None for keyword in call.keywords):
        return None
    for keyword in call.keywords:
        if keyword.arg == "name":
            value = keyword.value
            if not isinstance(value, ast.Constant):
                return None
            if not value.value:  # nox registers `name or func.__name__`
                return default
            return value.value if isinstance(value.value, str) else None
    return default


def _session_names(func: ast.FunctionDef, bound: dict[str, _Kind]) -> list[str]:
    """Return every name *func* is registered under as a nox session.

    Stacked ``@nox.session`` decorators each register an alias, applied
    bottom-up. Decorator expressions are evaluated top-down first, so each is
    checked against the bindings left by those above it; a call's own argument
    stores come after its callable is looked up. A decorator whose name cannot
    be read statically contributes nothing rather than a name ``nox -s`` would
    reject.
    """
    evaluated: list[tuple[ast.expr, dict[str, _Kind]]] = []
    for decorator in func.decorator_list:
        if isinstance(decorator, ast.Call):
            # The callable is looked up before its arguments run, so stores in
            # `@nox.session(tags=(nox := []))` don't affect this decorator.
            callee = _without(bound, _stored_names(decorator.func))
            evaluated.append((decorator, callee))
            bound = _without(bound, _stored_names(decorator))
        else:
            bound = _without(bound, _stored_names(decorator))
            evaluated.append((decorator, bound))
    names: list[str] = []
    for decorator, seen in reversed(evaluated):
        if _is_session_ref(decorator, seen):
            names.append(func.name)
        elif isinstance(decorator, ast.Call) and _is_session_ref(decorator.func, seen):
            name = _explicit_name(decorator, func.name)
            if name is not None:
                names.append(name)
    return names


def _first_line(func: ast.FunctionDef) -> str | None:
    lines = (ast.get_docstring(func) or "").strip().splitlines()
    return lines[0].strip() if lines else None


def _simple(node: ast.stmt, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
    """Apply a statement with no nested block to *bound*."""
    if _terminates(node):
        # Nothing after an unconditional `raise` (or `assert False`) runs.
        return None
    return _without(bound, _stored_names(node))


class _Scanner:
    """Follow a noxfile's import-time control flow without running it.

    Statements are processed in order while tracking which names are bound to
    nox, so a decorator only counts if nox is bound *at that point* on every
    path. Sessions are registered only where a definition always runs once
    reached: module level, class bodies, and ``finally`` blocks. ``if``/``match``
    branches, loops, and ``try``/``with`` bodies may be skipped or cut short by
    a handled exception, so definitions there are not listed; they are still
    followed for the bindings they may change.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.sessions: dict[str, str | None] = {}
        # Module bindings when the outermost enclosing class body began, if any.
        self._class_base: dict[str, _Kind] | None = None
        # A `global` (or `nonlocal`) declaration anywhere, e.g. in a class body
        # or a function called at import time, can rebind a module-level name
        # in a way statement order cannot follow, so such names never count.
        self._unstable = {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Global | ast.Nonlocal)
            for name in node.names
        }
        self._rebound = _bound_anywhere(tree)
        self._exception_classes = _exception_classes(tree, self._rebound)

    def run(
        self, body: list[ast.stmt], bound: dict[str, _Kind] | None, *, register: bool
    ) -> dict[str, _Kind] | None:
        """Return the bindings after *body*, or None if it always raises."""
        for node in body:
            if bound is None:
                break
            bound = self._statement(node, bound, register=register)
        return bound

    def _anywhere_in(
        self, body: list[ast.stmt], bound: dict[str, _Kind]
    ) -> dict[str, _Kind]:
        """Return bindings that hold however far *body* gets before stopping."""
        states = [bound]
        for node in body:
            after = self._statement(node, states[-1], register=False)
            if after is None:
                # The raising statement may still bind names first, e.g.
                # `raise E from (nox := ...)`, before control leaves it.
                states.append(_without(states[-1], _stored_names(node)))
                break
            states.append(after)
        return _merge(states)

    def _loop(self, body: list[ast.stmt], bound: dict[str, _Kind]) -> dict[str, _Kind]:
        # Bindings only shrink under _merge, so this reaches a fixed point.
        while True:
            after = _merge([bound, self._anywhere_in(body, bound)])
            if after == bound:
                return bound
            bound = after

    def _statement(
        self, node: ast.stmt, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind] | None:
        result: dict[str, _Kind] | None
        if isinstance(node, ast.Import | ast.ImportFrom):
            result = _apply_import(node, bound)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            result = self._function(node, bound, register=register)
        elif isinstance(node, ast.ClassDef):
            result = self._class(node, bound, register=register)
        elif isinstance(node, ast.Try | ast.TryStar):
            result = self._try(node, bound, register=register)
        elif isinstance(node, ast.If):
            result = self._if(node, bound)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.While):
            result = self._for_or_while(node, bound)
        elif isinstance(node, ast.Match):
            result = self._match(node, bound)
        elif isinstance(node, ast.With | ast.AsyncWith):
            bound = _without(bound, _stored_names(*node.items))
            # A context manager may suppress an exception partway through.
            result = self._anywhere_in(node.body, bound)
        else:
            result = _simple(node, bound)
        return None if result is None else _without(result, self._unstable)

    def _function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        bound: dict[str, _Kind],
        *,
        register: bool,
    ) -> dict[str, _Kind]:
        if register and isinstance(node, ast.FunctionDef):
            description = _first_line(node)
            for name in _session_names(node, bound):
                self.sessions[name] = description
        signature = _stored_names(*node.decorator_list, node.args, node.returns)
        return _without(bound, signature | {node.name})

    def _class(
        self, node: ast.ClassDef, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind] | None:
        header = _stored_names(*node.decorator_list, *node.bases, *node.keywords)
        bound = _without(bound, header)
        # A class body sees module bindings and binds its own names; a nested
        # class body does not see the enclosing class's names, only the module's
        # (which `global`-free class bodies cannot change).
        outer = self._class_base
        self._class_base = bound if outer is None else outer
        try:
            body = self.run(node.body, self._class_base, register=register)
        finally:
            self._class_base = outer
        if body is None:
            return None
        return _without(bound, {node.name})

    def _try(
        self, node: ast.Try | ast.TryStar, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind] | None:
        raised = self._anywhere_in(node.body, bound)
        body_done = self.run(node.body, bound, register=False)
        completed: dict[str, _Kind] | None = None
        if body_done is not None:
            completed = self.run(node.orelse, body_done, register=False)
            if completed is None:
                # An exception from `else` escapes these handlers.
                return self._escape(node, raised)
            # Any handler may still run after an implicit error in the body.
            handlers = node.handlers
        else:
            candidates = self._handlers_for_raise(node)
            if candidates is None:
                return self._escape(node, raised)
            handlers = candidates
        paths = [completed]
        for handler in handlers:
            names = _stored_names(handler.type)
            if handler.name:
                names.add(handler.name)
            start = _without(raised, names)
            after = self.run(handler.body, start, register=False)
            if after is None and body_done is None:
                # This handler may be the one the raise lands in, and it ends
                # the path too, so the import may always fail.
                return self._escape(node, self._anywhere_in(handler.body, start))
            # Python deletes an `except ... as name` target when the handler exits.
            paths.append(None if after is None else _without(after, names))
        after_try = _merge_paths(paths)
        if after_try is None:
            return self._escape(node, raised)
        return self.run(node.finalbody, after_try, register=register)

    def _escape(
        self, node: ast.Try | ast.TryStar, state: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        """Follow an exception leaving *node*.

        ``finally`` still runs, and a ``break`` or ``continue`` there may
        cancel the exception (under an unknown condition, possibly), so the
        path continues from it; otherwise the path ends. *state* is a
        conservative merge of the bindings where the exception may have been
        raised.
        """
        if not _breaks_in(node.finalbody, or_continues=True):
            return None
        return self.run(node.finalbody, state, register=False)

    def _handlers_for_raise(
        self, node: ast.Try | ast.TryStar
    ) -> list[ast.ExceptHandler] | None:
        """Return the handlers an always-raising body may land in, if one is sure.

        Only the first matching handler runs, and an earlier handler may match
        a subclass we cannot see, so every handler up to and including the
        first that is sure to catch every explicit raise is a candidate. None
        means no handler is sure to, so the import fails after ``finally``.
        """
        raises = self._raises_in(node.body)
        if not raises:
            return None
        candidates: list[ast.ExceptHandler] = []
        for handler in node.handlers:
            if _invalid_handler_type(handler):
                # Evaluating `except 1:` raises TypeError before any later
                # handler is tried, so the exception escapes.
                return None
            candidates.append(handler)
            if all(self._catches(handler, name) for name in raises):
                return candidates
        return None

    def _catches(self, handler: ast.ExceptHandler, raised: str | None) -> bool:
        return _catches(handler, raised, self._exception_classes)

    def _raises_in(self, body: list[ast.stmt]) -> list[str | None]:
        """Return the class names *body*'s explicit raises may let escape.

        ``assert False`` raises ``AssertionError``; an unknown class is None.
        Nested blocks and class bodies run at import; function bodies do not.
        A nested ``try`` drops raises its own handlers are sure to catch.
        """
        raises: list[str | None] = []
        for node in body:
            if isinstance(node, ast.Raise):
                raises.append(self._raise_class(node))
            elif isinstance(node, ast.Assert) and _terminates(node):
                raises.append("AssertionError")
            elif isinstance(node, ast.Try | ast.TryStar):
                raises += self._try_raises(node)
            elif not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for field in ("body", "orelse"):
                    raises += self._raises_in(getattr(node, field, []))
                for case in getattr(node, "cases", []):
                    raises += self._raises_in(case.body)
        return raises

    def _raise_class(self, node: ast.Raise) -> str | None:
        name = _raised_name(node)
        known = self._exception_classes.get(name) if name is not None else None
        if known is not None and known.line > node.lineno:
            return None  # Raised before its class is defined: NameError.
        return name

    def _try_raises(self, node: ast.Try | ast.TryStar) -> list[str | None]:
        """Return what escapes a nested ``try``: uncaught body raises and the rest."""
        inner = self._raises_in(node.body)
        if not any(_invalid_handler_type(h) for h in node.handlers):
            inner = [
                name
                for name in inner
                if not any(self._catches(h, name) for h in node.handlers)
            ]
        raises = inner + self._raises_in(node.orelse)
        for handler in node.handlers:
            raises += self._raises_in(handler.body)
        return raises + self._raises_in(node.finalbody)

    def _if(self, node: ast.If, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        bound = _without(bound, _stored_names(node.test))
        branches = [node.body, node.orelse]
        truth = _constant_truth(node.test)
        if truth is not None:
            # A constant condition takes exactly one branch (`if not False:`).
            branches = [node.body if truth else node.orelse]
        return _merge_paths([
            self.run(branch, bound, register=False) for branch in branches
        ])

    def _for_or_while(
        self, node: ast.For | ast.AsyncFor | ast.While, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        if _never_enters(node):
            # The body cannot run, so neither can its `break`: `else` always runs.
            header = _stored_names(
                node.test if isinstance(node, ast.While) else node.iter
            )
            return self.run(node.orelse, _without(bound, header), register=False)
        if isinstance(node, ast.While):
            header = _stored_names(node.test)
            if _always_true(node.test) and not _breaks_in(node.body):
                # `while True` without a `break` never finishes.
                return None
        else:
            header = _stored_names(node.target, node.iter)
            if (
                _always_enters(node)
                and not _breaks_in(node.body, or_continues=True)
                and self.run(node.body, _without(bound, header), register=False) is None
            ):
                # The first iteration always runs and always raises.
                return None
        bound = self._loop(node.body, _without(bound, header))
        after_else = self.run(node.orelse, bound, register=False)
        if not _breaks_in(node.body):
            return after_else  # Without a `break`, the loop always runs `else`.
        # A `break` skips `else`, so the loop state itself is a path too.
        return _merge_paths([bound, after_else])

    def _match(
        self, node: ast.Match, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        bound = _without(bound, _stored_names(node.subject))
        subject = (
            node.subject.value if isinstance(node.subject, ast.Constant) else _NO_VALUE
        )
        paths: list[dict[str, _Kind] | None] = []
        for case in node.cases:
            literal = (
                None if subject is _NO_VALUE else _literal_match(case.pattern, subject)
            )
            if literal is False:
                continue  # A constant subject can never match a different literal.
            # Captures are bound before the guard runs and stay bound if it (or
            # a partial pattern match) fails, so later cases and the
            # fallthrough lose them too.
            names = _stored_names(case.pattern, case.guard)
            bound = _without(bound, names)
            guard = _constant_truth(case.guard)
            if guard is False:
                continue  # A constant-false guard never runs the body.
            paths.append(self.run(case.body, bound, register=False))
            certain = _irrefutable(case.pattern) or literal is True
            if certain and (case.guard is None or guard is True):
                # `case _:`, a capture, or a literal equal to a constant subject
                # always matches, so later cases and fallthrough cannot happen.
                return _merge_paths(paths)
        paths.append(bound)  # No case matched.
        return _merge_paths(paths)


def parse_noxfile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract nox sessions from *text* by AST parsing, never importing it.

    ``nox --list`` imports and executes ``noxfile.py`` to build its registry, so
    discovery reads ``@nox.session``-decorated functions from the syntax tree
    instead. This is a safe subset: ``python=[...]`` and ``@nox.parametrize``
    variants are surfaced under their base name (``nox -s <name>`` still runs
    every variant), and sessions registered dynamically or only under a runtime
    condition are not discovered.
    """
    # The noxfile's own SyntaxWarnings (e.g. invalid escapes) are nox's to
    # report when it runs; nur only reads names, so keep them off every listing.
    with warnings.catch_warnings(action="ignore", category=SyntaxWarning):
        tree = ast.parse(text, filename=source_file)
        # Some trees parse but cannot compile (a repeated keyword argument, a
        # module-level `return`); nox fails to import those, so list nothing.
        # Compiling builds a code object without executing any of it.
        compile(tree, source_file, "exec", dont_inherit=True)
    if _mutates_session_attr(tree):
        return []
    scanner = _Scanner(tree)
    if scanner.run(tree.body, {}, register=True) is None:
        # The module always raises, so nox cannot import it at all.
        return []
    # A later definition under the same name replaces the earlier one, as in
    # nox's own registry, while keeping the first definition's position.
    return [
        Task(
            name=name,
            prefix="nox",
            argv_base=("nox", "-s", name),
            description=description,
            source_file=source_file,
            passthrough_prefix=("--",),
        )
        for name, description in scanner.sessions.items()
    ]


class NoxProvider:
    prefix = "nox"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            # Strict UTF-8, as nox itself reads the file before importing it, so
            # a file nox would reject (e.g. with a BOM) lists nothing.
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
            return parse_noxfile(text)
        except (
            OSError,
            UnicodeDecodeError,
            SyntaxError,
            ValueError,
            RecursionError,
        ) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
