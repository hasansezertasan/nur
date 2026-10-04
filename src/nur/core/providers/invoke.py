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
# Custom klass= is excluded because it may change naming/registration semantics.
_TASK_OPTIONS = frozenset({
    "name",
    "aliases",
    "positional",
    "optional",
    "default",
    "auto_shortflags",
    "help",
    "pre",
    "post",
    "autoprint",
    "iterable",
    "incrementable",
})


def _decorator_matches(expression: ast.expr, bindings: dict[str, str]) -> bool:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id) == "task"
    if not isinstance(expression, ast.Attribute) or expression.attr != "task":
        return False
    namespace = expression.value
    if isinstance(namespace, ast.Name):
        return bindings.get(namespace.id) in {"module", "tasks_module"}
    return (
        isinstance(namespace, ast.Attribute)
        and namespace.attr == "tasks"
        and isinstance(namespace.value, ast.Name)
        and bindings.get(namespace.value.id) == "module"
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
        if keyword.arg not in _TASK_OPTIONS:
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


def _normalize_name(name: str) -> str:
    return "".join(
        "-"
        if character == "_"
        and 0 < index < len(name) - 1
        and name[index - 1] != "."
        and name[index + 1] != "."
        else character
        for index, character in enumerate(name)
    )


def _task_names(function: ast.FunctionDef, bindings: dict[str, str]) -> list[str]:
    # Other decorators can replace the callable/name or discard the Task object.
    if len(function.decorator_list) != 1 or not (
        function.args.posonlyargs or function.args.args or function.args.vararg
    ):
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
        dict.fromkeys(_normalize_name(item) for item in [name, *aliases] if item)
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
            known = alias.name in {"invoke", "invoke.tasks"}
            kind = (
                "tasks_module"
                if alias.name == "invoke.tasks" and alias.asname
                else "module"
            )
        else:
            bound = alias.asname or alias.name
            known = not statement.level and (
                (
                    statement.module in {"invoke", "invoke.tasks"}
                    and alias.name == "task"
                )
                or (statement.module == "invoke" and alias.name == "tasks")
            )
            kind = "tasks_module" if alias.name == "tasks" else "task"
        bindings.pop(bound, None)
        if known:
            bindings[bound] = kind


def _module_children(node: ast.AST) -> list[ast.AST]:
    """Traverse expressions evaluated here, excluding nested local scopes."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        expressions: list[ast.AST] = [
            *node.args.defaults,
            *(value for value in node.args.kw_defaults if value is not None),
        ]
        if not isinstance(node, ast.Lambda):
            expressions.extend(node.decorator_list)
        return expressions
    if isinstance(node, ast.ClassDef):
        return [*node.decorator_list, *node.bases, *node.keywords]
    if isinstance(node, ast.AnnAssign):
        # An annotation alone neither assigns its target nor evaluates the
        # annotation expression under Python's deferred annotation semantics.
        if node.value is not None:
            return [node.target, node.value]
        # Non-simple annotations still evaluate their object/index expressions.
        return [] if isinstance(node.target, ast.Name) else [node.target]
    if isinstance(node, ast.comprehension):
        # The iteration target is local. Walrus assignments in the expressions
        # still bind in the containing scope and are visited normally.
        return [node.iter, *node.ifs]
    return list(ast.iter_child_nodes(node))


def _written_names(statement: ast.stmt) -> set[str]:
    """Over-approximate names a compound statement can replace."""
    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif (
            isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


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
        # Decorators are evaluated before function defaults. Read the task
        # decorator binding first, then apply writes from definition headers.
        names = (
            _task_names(statement, bindings)
            if isinstance(statement, ast.FunctionDef)
            else []
        )
        written = _written_names(statement)
        if "*" in written:
            bindings.clear()
            functions.clear()
        for bound in written:
            bindings.pop(bound, None)
            functions.pop(bound, None)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings)
        elif isinstance(statement, ast.FunctionDef):
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
