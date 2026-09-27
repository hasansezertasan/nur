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
    bottom-up. A decorator whose name cannot be read statically contributes
    nothing rather than a name ``nox -s`` would reject.
    """
    names: list[str] = []
    for decorator in reversed(func.decorator_list):
        if _is_session_ref(decorator, bound):
            names.append(func.name)
        elif isinstance(decorator, ast.Call) and _is_session_ref(decorator.func, bound):
            name = _explicit_name(decorator, func.name)
            if name is not None:
                names.append(name)
    return names


def _first_line(func: ast.FunctionDef) -> str | None:
    lines = (ast.get_docstring(func) or "").strip().splitlines()
    return lines[0].strip() if lines else None


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

    def __init__(self) -> None:
        self.sessions: dict[str, str | None] = {}

    def run(
        self, body: list[ast.stmt], bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind]:
        for node in body:
            bound = self._statement(node, bound, register=register)
        return bound

    def _anywhere_in(
        self, body: list[ast.stmt], bound: dict[str, _Kind]
    ) -> dict[str, _Kind]:
        """Return bindings that hold however far *body* gets before stopping."""
        states = [bound]
        for node in body:
            bound = self._statement(node, bound, register=False)
            states.append(bound)
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
    ) -> dict[str, _Kind]:
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
            result = _without(bound, _stored_names(node))
        return result

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
    ) -> dict[str, _Kind]:
        header = _stored_names(*node.decorator_list, *node.bases, *node.keywords)
        bound = _without(bound, header)
        # The class body sees module bindings but binds its own names.
        self.run(node.body, bound, register=register)
        return _without(bound, {node.name})

    def _try(
        self, node: ast.Try | ast.TryStar, bound: dict[str, _Kind], *, register: bool
    ) -> dict[str, _Kind]:
        completed = self.run(
            node.orelse, self.run(node.body, bound, register=False), register=False
        )
        raised = self._anywhere_in(node.body, bound)
        paths = [completed]
        for handler in node.handlers:
            names = _stored_names(handler.type)
            if handler.name:
                names.add(handler.name)
            paths.append(
                self.run(handler.body, _without(raised, names), register=False)
            )
        return self.run(node.finalbody, _merge(paths), register=register)

    def _if(self, node: ast.If, bound: dict[str, _Kind]) -> dict[str, _Kind]:
        bound = _without(bound, _stored_names(node.test))
        return _merge([
            self.run(node.body, bound, register=False),
            self.run(node.orelse, bound, register=False),
        ])

    def _for_or_while(
        self, node: ast.For | ast.AsyncFor | ast.While, bound: dict[str, _Kind]
    ) -> dict[str, _Kind]:
        if isinstance(node, ast.While):
            header = _stored_names(node.test)
        else:
            header = _stored_names(node.target, node.iter)
        bound = self._loop(node.body, _without(bound, header))
        return _merge([bound, self.run(node.orelse, bound, register=False)])

    def _match(self, node: ast.Match, bound: dict[str, _Kind]) -> dict[str, _Kind]:
        bound = _without(bound, _stored_names(node.subject))
        paths = [bound]
        for case in node.cases:
            names = _stored_names(case.pattern, case.guard)
            paths.append(self.run(case.body, _without(bound, names), register=False))
        return _merge(paths)


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
    scanner = _Scanner()
    scanner.run(tree.body, {}, register=True)
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
