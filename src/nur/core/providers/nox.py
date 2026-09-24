from __future__ import annotations

import ast
import logging
import warnings
from typing import TYPE_CHECKING, Literal

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["NoxProvider", "parse_noxfile"]


log = logging.getLogger("nur")

_SOURCE_FILE = "noxfile.py"


# What an imported name refers to: the ``nox`` module, ``nox.session``, or
# anything else (tracked so a fallback import still counts as a binding).
type _Kind = Literal["module", "session", "other"]


def _import_bindings(node: ast.Import | ast.ImportFrom) -> dict[str, _Kind]:
    bound: dict[str, _Kind] = {}
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.asname:
                bound[alias.asname] = "module" if alias.name == "nox" else "other"
            else:
                # `import nox.command` binds the top-level `nox` name too.
                top = alias.name.partition(".")[0]
                bound[top] = "module" if top == "nox" else "other"
    else:
        from_nox = node.module == "nox" and not node.level
        for alias in node.names:
            if alias.name == "*":
                if from_nox:
                    bound["session"] = "session"
            else:
                is_session = from_nox and alias.name == "session"
                bound[alias.asname or alias.name] = "session" if is_session else "other"
    return bound


def _on_every_path(paths: list[dict[str, _Kind]]) -> dict[str, _Kind]:
    """Keep names every path binds, preferring a nox binding when paths differ.

    ``try: from nox_uv import session`` / ``except ImportError: from nox import
    session`` binds ``session`` either way, to nox or a drop-in wrapper of it.
    """
    common: set[str] = set.intersection(*(set(path) for path in paths))
    bound: dict[str, _Kind] = {}
    for name in common:
        bound[name] = "other"
        for path in paths:
            if path[name] != "other":
                bound[name] = path[name]
                break
    return bound


def _bound_on_every_path(body: list[ast.stmt]) -> dict[str, _Kind]:
    """Return the names *body*'s imports bind however its branches go.

    A name bound only in an ``if`` without a binding ``else``, a loop, or a
    ``match`` case may be missing at runtime, making nox fail with
    ``NameError``. Both sides of an ``if``/``else`` count, as does a ``try``
    whose body and every handler bind it (the ``except ImportError`` fallback
    idiom). Class bodies bind class attributes, not module names.
    """
    bound: dict[str, _Kind] = {}
    for node in body:
        if isinstance(node, ast.Import | ast.ImportFrom):
            bound |= _import_bindings(node)
        elif isinstance(node, ast.With):
            bound |= _bound_on_every_path(node.body)
        elif isinstance(node, ast.If):
            bound |= _on_every_path([
                _bound_on_every_path(node.body),
                _bound_on_every_path(node.orelse),
            ])
        elif isinstance(node, ast.Try | ast.TryStar):
            paths = [_bound_on_every_path(node.body + node.orelse)]
            paths += [_bound_on_every_path(handler.body) for handler in node.handlers]
            bound |= _on_every_path(paths)
            bound |= _bound_on_every_path(node.finalbody)
    return bound


def _module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Walk the statements that always run when the module is imported.

    Only ``with`` and ``class`` bodies, and a ``try``'s body, ``else``, and
    ``finally`` are entered. ``if``/``match`` branches, loops, and ``except``
    handlers run only when a runtime condition holds, so a session defined
    there may not exist (``nox -s`` would reject it); function bodies run only
    when called.

    Yields:
        Each statement in *body*, then those nested in the entered blocks.
    """
    for node in body:
        yield node
        if isinstance(node, ast.With | ast.ClassDef):
            yield from _module_level(node.body)
        elif isinstance(node, ast.Try | ast.TryStar):
            yield from _module_level(node.body)
            yield from _module_level(node.orelse)
            yield from _module_level(node.finalbody)


def _is_session_ref(node: ast.expr, modules: set[str], decorators: set[str]) -> bool:
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "session"
            and isinstance(node.value, ast.Name)
            and node.value.id in modules
        )
    return isinstance(node, ast.Name) and node.id in decorators


def _session_names(
    func: ast.FunctionDef, modules: set[str], decorators: set[str]
) -> list[str]:
    """Return every name *func* is registered under as a nox session.

    Stacked ``@nox.session`` decorators each register an alias, applied
    bottom-up. A decorator whose name cannot be read statically contributes
    nothing rather than a name ``nox -s`` would reject.
    """
    names: list[str] = []
    for decorator in reversed(func.decorator_list):
        if _is_session_ref(decorator, modules, decorators):
            names.append(func.name)
        elif isinstance(decorator, ast.Call) and _is_session_ref(
            decorator.func, modules, decorators
        ):
            name = _explicit_name(decorator, func.name)
            if name is not None:
                names.append(name)
    return names


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


def _first_line(func: ast.FunctionDef) -> str | None:
    lines = (ast.get_docstring(func) or "").strip().splitlines()
    return lines[0].strip() if lines else None


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
    bound = _bound_on_every_path(tree.body)
    modules = {name for name, kind in bound.items() if kind == "module"}
    decorators = {name for name, kind in bound.items() if kind == "session"}
    if not modules and not decorators:
        return []
    # A later definition under the same name replaces the earlier one, as in
    # nox's own registry, while keeping the first definition's position.
    sessions: dict[str, str | None] = {}
    for node in _module_level(tree.body):
        if isinstance(node, ast.FunctionDef):
            description = _first_line(node)
            for name in _session_names(node, modules, decorators):
                sessions[name] = description
    return [
        Task(
            name=name,
            prefix="nox",
            argv_base=("nox", "-s", name),
            description=description,
            source_file=source_file,
            passthrough_prefix=("--",),
        )
        for name, description in sessions.items()
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
