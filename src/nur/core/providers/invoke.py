"""Discover Invoke tasks by parsing Python, without importing project code."""

from __future__ import annotations

import ast
import logging
import tokenize
import warnings
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["InvokeProvider", "parse_tasks"]

log = logging.getLogger("nur")
_SOURCE_FILE = "tasks.py"


def _decorator_matches(expression: ast.expr, bindings: dict[str, str]) -> bool:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id) == "task"
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "task"
        and isinstance(expression.value, ast.Name)
        and bindings.get(expression.value.id) == "module"
    )


def _literal_aliases(expression: ast.expr) -> list[str] | None:
    if not isinstance(expression, (ast.Tuple, ast.List)):
        return None
    aliases: list[str] = []
    for element in expression.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        aliases.append(element.value)
    return aliases


def _literal_metadata(decorator: ast.expr, name: str) -> tuple[str, list[str]] | None:
    aliases: list[str] = []
    if not isinstance(decorator, ast.Call):
        return name, aliases
    for keyword in decorator.keywords:
        if keyword.arg in {None, "klass"}:
            return None
        if keyword.arg == "name":
            if not isinstance(keyword.value, ast.Constant):
                return None
            value = keyword.value.value
            if value is not None and not isinstance(value, str):
                return None
            name = value or name
        elif keyword.arg == "aliases":
            parsed = _literal_aliases(keyword.value)
            if parsed is None:
                return None
            aliases = parsed
    return name, aliases


def _task_names(function: ast.FunctionDef, bindings: dict[str, str]) -> list[str]:
    # Other decorators can replace the callable/name or discard the Task object.
    if len(function.decorator_list) != 1:
        return []
    decorator = function.decorator_list[0]
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not _decorator_matches(expression, bindings):
        return []
    metadata = _literal_metadata(decorator, function.name)
    if metadata is None:
        return []
    name, aliases = metadata
    # Invoke's default Collection turns underscores into dashes. Config and
    # explicit Collection wiring are outside this single-module subset.
    return list(
        dict.fromkeys(item.replace("_", "-") for item in [name, *aliases] if item)
    )


def _bind_import(
    statement: ast.Import | ast.ImportFrom, bindings: dict[str, str]
) -> None:
    for alias in statement.names:
        if alias.name == "*":
            bindings.clear()
            continue
        if isinstance(statement, ast.Import):
            bound = alias.asname or alias.name.split(".")[0]
            known = alias.name == "invoke"
            kind = "module"
        else:
            bound = alias.asname or alias.name
            known = (
                not statement.level
                and statement.module in {"invoke", "invoke.tasks"}
                and alias.name == "task"
            )
            kind = "task"
        bindings.pop(bound, None)
        if known:
            bindings[bound] = kind


def parse_tasks(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Read top-level decorated functions and literal names/aliases.

    Imports are tracked in source order, never executed. Nested/conditional
    definitions, custom decorators, computed metadata and Collection wiring
    are not resolved. Task bodies and pre/post hooks remain Invoke's concern.
    """
    with warnings.catch_warnings(action="ignore", category=SyntaxWarning):
        tree = ast.parse(text, filename=source_file)
        compile(tree, source_file, "exec", dont_inherit=True)
    bindings: dict[str, str] = {}
    functions: dict[str, list[Task]] = {}
    for statement in tree.body:
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings)
            for alias in statement.names:
                bound = alias.asname or alias.name.split(".")[0]
                functions.pop(bound, None)
        elif isinstance(statement, ast.FunctionDef):
            names = _task_names(statement, bindings)
            docstring = ast.get_docstring(statement)
            description = docstring.splitlines()[0] if docstring else None
            functions[statement.name] = [
                Task(
                    name=name,
                    prefix="invoke",
                    argv_base=("invoke", name),
                    description=description,
                    source_file=source_file,
                )
                for name in names
            ]
            bindings.pop(statement.name, None)
        elif isinstance(statement, (ast.ClassDef, ast.AsyncFunctionDef)):
            bindings.pop(statement.name, None)
            functions.pop(statement.name, None)
        else:
            # Never infer bindings created by compound statements. Invalidate
            # names they may overwrite, while leaving unrelated tasks visible.
            for node in ast.walk(statement):
                if isinstance(node, ast.Name) and isinstance(
                    node.ctx, (ast.Store, ast.Del)
                ):
                    bindings.pop(node.id, None)
                    functions.pop(node.id, None)
    tasks = {task.name: task for group in functions.values() for task in group}
    return list(tasks.values())


class InvokeProvider:
    prefix = "invoke"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            # Respect Python's source encoding cookie, as Invoke's importer does.
            with tokenize.open(cwd / _SOURCE_FILE) as source:
                return parse_tasks(source.read())
        except (OSError, UnicodeError, SyntaxError, ValueError, RecursionError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
