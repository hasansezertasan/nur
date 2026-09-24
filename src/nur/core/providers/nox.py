from __future__ import annotations

import ast
import logging
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["NoxProvider", "parse_noxfile"]


log = logging.getLogger("nur")

_SOURCE_FILE = "noxfile.py"


def _nox_aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return (names bound to the ``nox`` module, names bound to ``nox.session``)."""
    modules: set[str] = set()
    decorators: set[str] = set()
    for node in _module_level(tree.body):
        if isinstance(node, ast.Import):
            modules.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "nox"
            )
        elif (
            isinstance(node, ast.ImportFrom) and node.module == "nox" and not node.level
        ):
            decorators.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name == "session"
            )
    return modules, decorators


def _module_level(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Walk the statements that run when the module is imported.

    Function and class bodies are skipped: a session defined inside a factory
    only exists once that code runs, which static discovery must not assume.

    Yields:
        Each statement in *body*, then those nested in ``if``/``try``/``with``.
    """
    for node in body:
        yield node
        if isinstance(node, ast.If | ast.With):
            yield from _module_level(node.body)
            if isinstance(node, ast.If):
                yield from _module_level(node.orelse)
        elif isinstance(node, ast.Try | ast.TryStar):
            yield from _module_level(node.body)
            for handler in node.handlers:
                yield from _module_level(handler.body)
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


def _session_name(
    func: ast.FunctionDef, modules: set[str], decorators: set[str]
) -> str | None:
    """Return the runnable session name for *func*, or None if it is not one.

    An explicit ``name=`` that is not a string literal makes the real name
    unknowable without evaluation, so such a session is skipped rather than
    shown under a name ``nox -s`` would reject.
    """
    for decorator in func.decorator_list:
        if _is_session_ref(decorator, modules, decorators):
            return func.name
        if isinstance(decorator, ast.Call) and _is_session_ref(
            decorator.func, modules, decorators
        ):
            for keyword in decorator.keywords:
                if keyword.arg == "name":
                    value = keyword.value
                    if isinstance(value, ast.Constant) and value.value is None:
                        return func.name
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value or None
                    return None
            return func.name
    return None


def _first_line(func: ast.FunctionDef) -> str | None:
    doc = ast.get_docstring(func)
    if not doc:
        return None
    return doc.strip().splitlines()[0].strip() or None


def parse_noxfile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract nox sessions from *text* by AST parsing, never importing it.

    ``nox --list`` imports and executes ``noxfile.py`` to build its registry, so
    discovery reads ``@nox.session``-decorated functions from the syntax tree
    instead. This is a safe subset: ``python=[...]`` and ``@nox.parametrize``
    variants are surfaced under their base name (``nox -s <name>`` still runs
    every variant), and sessions registered dynamically are not discovered.
    """
    tree = ast.parse(text, filename=source_file)
    modules, decorators = _nox_aliases(tree)
    if not modules and not decorators:
        return []
    # A later definition under the same name replaces the earlier one, as in
    # nox's own registry, while keeping the first definition's position.
    sessions: dict[str, str | None] = {}
    for node in _module_level(tree.body):
        if not isinstance(node, ast.FunctionDef):
            continue
        name = _session_name(node, modules, decorators)
        if name is not None:
            sessions[name] = _first_line(node)
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
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
            return parse_noxfile(text)
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
