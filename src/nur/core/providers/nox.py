from __future__ import annotations

import ast
import builtins
import enum
import logging
import warnings
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._pystatic import (
    NO_VALUE,
    always_diverts,
    always_enters,
    always_true,
    bound_anywhere,
    breaks_in,
    cannot_catch,
    catches,
    constant_truth,
    exception_classes,
    invalid_handler_type,
    irrefutable,
    literal_match,
    never_enters,
    raised_name,
    signed_number,
    stored_names,
    terminates,
)

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
    ``del nox.session``, ``setattr``/``delattr``, or a namespace via
    ``__dict__``/``vars()``/``globals()``/``locals()``) no decorator can be
    trusted. Other attributes
    (``nox.options``, ``nox.needs_version``) do not count.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            stored = isinstance(node.ctx, ast.Store | ast.Del)
            if (node.attr == "session" and stored) or node.attr == "__dict__":
                return True
        elif _reflective_session_write(node) or _calls_any(node, _NAMESPACES):
            return True
    return False


_BUILTIN_NAMES = frozenset(dir(builtins))
# Builtins that expose a namespace as a mutable mapping.
_NAMESPACES = frozenset({"vars", "globals", "locals"})


def _calls_any(node: ast.AST, names: frozenset[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in names
    )


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
            callee = _without(bound, stored_names(decorator.func))
            evaluated.append((decorator, callee))
            bound = _without(bound, stored_names(decorator))
        else:
            bound = _without(bound, stored_names(decorator))
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
    if terminates(node):
        # Nothing after an unconditional `raise` (or `assert False`) runs.
        return None
    return _without(bound, stored_names(node))


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
        self._rebound = bound_anywhere(tree)
        self._exception_classes = exception_classes(tree, self._rebound)
        self._star_import = any(
            isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
            for node in ast.walk(tree)
        )

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
                states.append(_without(states[-1], stored_names(node)))
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
            bound = _without(bound, stored_names(*node.items))
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
        signature = stored_names(*node.decorator_list, node.args, node.returns)
        return _without(bound, signature | {node.name})

    def _class(
        self, node: ast.ClassDef, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind] | None:
        header = stored_names(*node.decorator_list, *node.bases, *node.keywords)
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
            names = stored_names(handler.type)
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
        if not breaks_in(
            node.finalbody, or_continues=True, known=self._exception_classes
        ):
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
        if isinstance(node, ast.TryStar):
            # `except*` wraps raises in groups, can run several handlers, and
            # rejects `except* ExceptionGroup`; it is never trusted to catch.
            return None
        raises = self._raises_in(node.body)
        if not raises:
            return None
        candidates: list[ast.ExceptHandler] = []
        for handler in node.handlers:
            if invalid_handler_type(handler) or self._unbound_handler(handler):
                # Evaluating `except 1:` (TypeError) or a name not bound yet
                # (NameError) raises before any later handler is tried, so the
                # exception escapes.
                return None
            if all(
                cannot_catch(handler, name, self._exception_classes) for name in raises
            ):
                continue  # Provably can't catch any of them, so it never runs.
            candidates.append(handler)
            if all(self._catches(handler, name) for name in raises):
                return candidates
        return None

    def _unbound_handler(self, handler: ast.ExceptHandler) -> bool:
        """Return True if a handler names something certainly unbound there.

        That is a user exception class first defined after the handler, or a
        name the file never binds that is not a builtin (with no star import
        that could supply it).
        """
        if handler.type is None:
            return False
        types = (
            handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
        )
        for t in types:
            if not isinstance(t, ast.Name):
                continue
            known = self._exception_classes.get(t.id)
            if known is not None and known.line > handler.lineno:
                return True
            if not self._star_import and t.id not in self._rebound | _BUILTIN_NAMES:
                return True
        return False

    def _catches(self, handler: ast.ExceptHandler, raised: str | None) -> bool:
        return catches(handler, raised, self._exception_classes)

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
            elif isinstance(node, ast.Assert) and terminates(node):
                raises.append("AssertionError")
            elif isinstance(node, ast.Try | ast.TryStar):
                raises += self._try_raises(node)
            elif not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for field in ("body", "orelse"):
                    raises += self._raises_in(getattr(node, field, []))
                for case in getattr(node, "cases", []):
                    raises += self._raises_in(case.body)
            if always_diverts([node], self._exception_classes):
                break  # Nothing after it in this block runs.
        return raises

    def _raise_class(self, node: ast.Raise) -> str | None:
        name = raised_name(node)
        known = self._exception_classes.get(name) if name is not None else None
        if known is not None and known.line > node.lineno:
            return None  # Raised before its class is defined: NameError.
        return name

    def _try_raises(self, node: ast.Try | ast.TryStar) -> list[str | None]:
        """Return what escapes a nested ``try``: uncaught body raises and the rest."""
        inner = self._raises_in(node.body)
        trusted = isinstance(node, ast.Try)  # `except*` is never trusted to catch.
        if inner and any(
            invalid_handler_type(h) or self._unbound_handler(h) for h in node.handlers
        ):
            # Evaluating such a handler raises TypeError/NameError, which
            # replaces the exception on its way out, so its class is unknown.
            inner = [None]
        elif trusted:
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
        bound = _without(bound, stored_names(node.test))
        branches = [node.body, node.orelse]
        truth = constant_truth(node.test)
        if truth is not None:
            # A constant condition takes exactly one branch (`if not False:`).
            branches = [node.body if truth else node.orelse]
        return _merge_paths([
            self.run(branch, bound, register=False) for branch in branches
        ])

    def _for_or_while(
        self, node: ast.For | ast.AsyncFor | ast.While, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        if never_enters(node):
            # The body cannot run, so neither can its `break`: `else` always runs.
            header = stored_names(
                node.test if isinstance(node, ast.While) else node.iter
            )
            return self.run(node.orelse, _without(bound, header), register=False)
        if isinstance(node, ast.While):
            header = stored_names(node.test)
            if always_true(node.test) and not breaks_in(
                node.body, known=self._exception_classes
            ):
                # `while True` without a `break` never finishes.
                return None
        else:
            header = stored_names(node.target, node.iter)
            if (
                always_enters(node)
                and not breaks_in(
                    node.body, or_continues=True, known=self._exception_classes
                )
                and self.run(node.body, _without(bound, header), register=False) is None
            ):
                # The first iteration always runs and always raises.
                return None
        bound = self._loop(node.body, _without(bound, header))
        after_else = self.run(node.orelse, bound, register=False)
        if not breaks_in(node.body, known=self._exception_classes):
            return after_else  # Without a `break`, the loop always runs `else`.
        # A `break` skips `else`, so the loop state itself is a path too.
        return _merge_paths([bound, after_else])

    def _match(
        self, node: ast.Match, bound: dict[str, _Kind]
    ) -> dict[str, _Kind] | None:
        bound = _without(bound, stored_names(node.subject))
        subject = signed_number(node.subject)
        paths: list[dict[str, _Kind] | None] = []
        for case in node.cases:
            literal = (
                None if subject is NO_VALUE else literal_match(case.pattern, subject)
            )
            if literal is False:
                continue  # A constant subject can never match a different literal.
            # Captures are bound before the guard runs and stay bound if it (or
            # a partial pattern match) fails, so later cases and the
            # fallthrough lose them too.
            names = stored_names(case.pattern, case.guard)
            bound = _without(bound, names)
            guard = constant_truth(case.guard)
            if guard is False:
                continue  # A constant-false guard never runs the body.
            paths.append(self.run(case.body, bound, register=False))
            certain = irrefutable(case.pattern) or literal is True
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
