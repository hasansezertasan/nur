from __future__ import annotations

import ast
import logging
import warnings
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["NoxProvider", "parse_noxfile"]


log = logging.getLogger("nur")

_SOURCE_FILE = "noxfile.py"


def _nox_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return (names bound to the ``nox`` module, names bound to ``nox.session``).

    Imports are collected from every module-level branch, e.g. a
    ``try: ... except ImportError: from nox import session`` fallback. That only
    widens which decorators are recognised; whether a session is listed still
    depends on where its ``def`` sits.
    """
    modules: set[str] = set()
    decorators: set[str] = set()
    for node in _module_level(tree.body, include_conditional=True):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "nox":
                    modules.add(alias.asname or "nox")
                elif alias.name.startswith("nox.") and not alias.asname:
                    # `import nox.command` binds the top-level `nox` name too.
                    modules.add("nox")
        elif (
            isinstance(node, ast.ImportFrom) and node.module == "nox" and not node.level
        ):
            for alias in node.names:
                if alias.name == "session":
                    decorators.add(alias.asname or "session")
                elif alias.name == "*":
                    decorators.add("session")
    return modules, decorators


def _module_level(
    body: list[ast.stmt], *, include_conditional: bool = False
) -> Iterator[ast.stmt]:
    """Walk the statements that run when the module is imported.

    By default only blocks that always run are entered: ``with`` and ``class``
    bodies, and a ``try``'s body, ``else``, and ``finally``. ``if``/``match``
    branches, loops, and ``except`` handlers run only when a runtime condition
    holds, so a session defined there may not exist (``nox -s`` would reject
    it) and they are entered only with *include_conditional*. Function bodies
    run only when called and are never entered.

    Yields:
        Each statement in *body*, then those nested in the entered blocks.
    """
    for node in body:
        yield node
        children: list[list[ast.stmt]] = []
        if isinstance(node, ast.With | ast.ClassDef):
            children.append(node.body)
        elif isinstance(node, ast.Try | ast.TryStar):
            children += [node.body, node.orelse, node.finalbody]
            if include_conditional:
                children += [handler.body for handler in node.handlers]
        elif include_conditional and isinstance(node, ast.If | ast.For | ast.While):
            children += [node.body, node.orelse]
        elif include_conditional and isinstance(node, ast.Match):
            children += [case.body for case in node.cases]
        for child in children:
            yield from _module_level(child, include_conditional=include_conditional)


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
    if call.args:
        return None
    for keyword in call.keywords:
        if keyword.arg is None:
            return None
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
    modules, decorators = _nox_aliases(tree)
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
