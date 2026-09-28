from __future__ import annotations

import ast
import enum
import logging
import warnings
from typing import TYPE_CHECKING

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


# Exceptions that `except Exception` does not catch.
_BASE_ONLY = frozenset({"SystemExit", "KeyboardInterrupt", "GeneratorExit"})


def _raised_name(node: ast.Raise) -> str | None:
    """Return the class name an explicit ``raise X`` / ``raise X(...)`` raises."""
    exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
    return exc.id if isinstance(exc, ast.Name) else None


def _catches(handler: ast.ExceptHandler, raised: str | None, rebound: set[str]) -> bool:
    """Return True if *handler* certainly catches an exception named *raised*.

    The same name in ``raise X`` and ``except X`` always matches: nothing runs
    between the two lookups. ``Exception``/``BaseException`` are trusted only
    if the file never rebinds them (``Exception = ValueError``), and the
    ``Exception`` rule also needs an unshadowed raised name.
    """
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = {t.id for t in types if isinstance(t, ast.Name)}
    if raised is not None and raised in names:
        return True
    builtins = names - rebound
    if "BaseException" in builtins:
        return True
    return (
        "Exception" in builtins
        and raised is not None
        and raised not in rebound
        and raised not in _BASE_ONLY
    )


def _bound_anywhere(tree: ast.Module) -> set[str]:
    """Return every name the file binds anywhere, in any scope."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            names.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.partition(".")[0])
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _raises_in(body: list[ast.stmt]) -> list[ast.Raise]:
    """Return the explicit ``raise`` statements *body* may run at import time.

    Nested blocks and class bodies run at import; function bodies do not.
    """
    raises: list[ast.Raise] = []
    for node in body:
        if isinstance(node, ast.Raise):
            raises.append(node)
        elif not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for field in ("body", "orelse", "finalbody"):
                raises += _raises_in(getattr(node, field, []))
            for handler in getattr(node, "handlers", []):
                raises += _raises_in(handler.body)
            for case in getattr(node, "cases", []):
                raises += _raises_in(case.body)
    return raises


def _always_true(test: ast.expr) -> bool:
    return isinstance(test, ast.Constant) and bool(test.value)


def _breaks_in(body: list[ast.stmt]) -> bool:
    """Return True if *body* contains a ``break`` for the enclosing loop."""
    for node in body:
        if isinstance(node, ast.Break):
            return True
        # A nested loop's `break` and a function's body belong elsewhere.
        if isinstance(
            node,
            ast.For
            | ast.AsyncFor
            | ast.While
            | ast.FunctionDef
            | ast.AsyncFunctionDef
            | ast.ClassDef,
        ):
            continue
        blocks = [getattr(node, field, []) for field in ("body", "orelse", "finalbody")]
        blocks += [handler.body for handler in getattr(node, "handlers", [])]
        blocks += [case.body for case in getattr(node, "cases", [])]
        if any(_breaks_in(block) for block in blocks):
            return True
    return False


def _stored_names(*nodes: ast.AST | None) -> set[str]:
    """Return every name *nodes* may bind or delete (assignment, walrus, ``as``)."""
    names: set[str] = set()
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(
                child.ctx, ast.Store | ast.Del
            ):
                names.add(child.id)
            elif isinstance(child, ast.MatchAs | ast.MatchStar) and child.name:
                names.add(child.name)
            elif isinstance(child, ast.MatchMapping) and child.rest:
                names.add(child.rest)
    return names


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
    checked against the bindings left by those above it (and itself, should it
    rebind a name with ``:=``). A decorator whose name cannot be read statically
    contributes nothing rather than a name ``nox -s`` would reject.
    """
    evaluated: list[tuple[ast.expr, dict[str, _Kind]]] = []
    for decorator in func.decorator_list:
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
    if isinstance(node, ast.Raise):
        # Nothing after an unconditional `raise` runs on this path.
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
        # The class body sees module bindings but binds its own names.
        if self.run(node.body, bound, register=register) is None:
            return None
        return _without(bound, {node.name})

    def _try(
        self, node: ast.Try | ast.TryStar, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind] | None:
        body_done = self.run(node.body, bound, register=False)
        completed: dict[str, _Kind] | None = None
        if body_done is not None:
            completed = self.run(node.orelse, body_done, register=False)
            if completed is None:
                # An exception from `else` escapes these handlers.
                return None
            # Any handler may still run after an implicit error in the body.
            handlers = node.handlers
        else:
            candidates = self._handlers_for_raise(node)
            if candidates is None:
                return None
            handlers = candidates
        raised = self._anywhere_in(node.body, bound)
        paths = [completed]
        for handler in handlers:
            names = _stored_names(handler.type)
            if handler.name:
                names.add(handler.name)
            after = self.run(handler.body, _without(raised, names), register=False)
            if after is None and body_done is None:
                # This handler may be the one the raise lands in, and it ends
                # the path too, so the import may always fail.
                return None
            # Python deletes an `except ... as name` target when the handler exits.
            paths.append(None if after is None else _without(after, names))
        # `finally` runs even when every path raises, but the import then fails.
        after_try = _merge_paths(paths)
        final = self.run(node.finalbody, after_try or {}, register=register)
        return None if after_try is None else final

    def _handlers_for_raise(
        self, node: ast.Try | ast.TryStar
    ) -> list[ast.ExceptHandler] | None:
        """Return the handlers an always-raising body may land in, if one is sure.

        Only the first matching handler runs, and an earlier handler may match
        a subclass we cannot see, so every handler up to and including the
        first that is sure to catch every explicit raise is a candidate. None
        means no handler is sure to, so the import fails after ``finally``.
        """
        raises = [_raised_name(r) for r in _raises_in(node.body)]
        if not raises:
            return None
        candidates: list[ast.ExceptHandler] = []
        for handler in node.handlers:
            candidates.append(handler)
            if all(_catches(handler, name, self._rebound) for name in raises):
                return candidates
        return None

    def _if(self, node: ast.If, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        bound = _without(bound, _stored_names(node.test))
        return _merge_paths([
            self.run(node.body, bound, register=False),
            self.run(node.orelse, bound, register=False),
        ])

    def _for_or_while(
        self, node: ast.For | ast.AsyncFor | ast.While, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        if isinstance(node, ast.While):
            header = _stored_names(node.test)
            if _always_true(node.test) and not _breaks_in(node.body):
                # `while True` without a `break` never finishes.
                return None
        else:
            header = _stored_names(node.target, node.iter)
        bound = self._loop(node.body, _without(bound, header))
        # The loop may break before `else`, so `bound` itself is a path too.
        return _merge_paths([bound, self.run(node.orelse, bound, register=False)])

    def _match(
        self, node: ast.Match, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        bound = _without(bound, _stored_names(node.subject))
        paths: list[dict[str, _Kind] | None] = [bound]
        for case in node.cases:
            names = _stored_names(case.pattern, case.guard)
            paths.append(self.run(case.body, _without(bound, names), register=False))
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
