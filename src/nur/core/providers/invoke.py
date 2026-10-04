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
_ATTRIBUTE_ARGS_MIN = 2
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


def _decorator_matches(
    expression: ast.expr, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id) == "task"
    if not isinstance(expression, ast.Attribute) or expression.attr != "task":
        return False
    kind = _module_kind(expression.value, bindings, tainted)
    export = {"module": "invoke.task", "tasks_module": "invoke.tasks.task"}.get(
        kind or ""
    )
    return export is not None and export not in tainted


def _module_kind(
    namespace: ast.expr, bindings: dict[str, str], tainted: set[str]
) -> str | None:
    if isinstance(namespace, ast.Name):
        kind = bindings.get(namespace.id)
        return kind if kind in {"module", "tasks_module"} else None
    if (
        isinstance(namespace, ast.Attribute)
        and namespace.attr == "tasks"
        and isinstance(namespace.value, ast.Name)
        and bindings.get(namespace.value.id) == "module"
        and "invoke.tasks" not in tainted
    ):
        return "tasks_module"
    return None


def _mutated_export(
    node: ast.AST, bindings: dict[str, str], tainted: set[str]
) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        namespace, attribute = node.value, node.attr
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"setattr", "delattr"}
        and len(node.args) >= _ATTRIBUTE_ARGS_MIN
        and isinstance(node.args[1], ast.Constant)
    ):
        value = node.args[1].value
        if not isinstance(value, str):
            return None
        namespace, attribute = node.args[0], value
    else:
        return None
    kind = _module_kind(namespace, bindings, tainted)
    return {
        ("module", "task"): "invoke.task",
        ("module", "tasks"): "invoke.tasks",
        ("tasks_module", "task"): "invoke.tasks.task",
    }.get((kind or "", attribute))


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


def _task_names(
    function: ast.FunctionDef, bindings: dict[str, str], tainted: set[str]
) -> list[str]:
    # Other decorators can replace the callable/name or discard the Task object.
    if len(function.decorator_list) != 1 or not (
        function.args.posonlyargs or function.args.args or function.args.vararg
    ):
        return []
    decorator = function.decorator_list[0]
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not _decorator_matches(expression, bindings, tainted):
        return []
    if (
        isinstance(decorator, ast.Call)
        and decorator.args
        and any(keyword.arg == "pre" for keyword in decorator.keywords)
    ):
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


def _import_binding(
    statement: ast.Import | ast.ImportFrom, alias: ast.alias, tainted: set[str]
) -> tuple[str, str | None]:
    if isinstance(statement, ast.Import):
        bound = alias.asname or alias.name.split(".")[0]
        if alias.name == "invoke" or (
            alias.name == "invoke.tasks" and not alias.asname
        ):
            return bound, "module"
        kind = (
            "tasks_module"
            if alias.name == "invoke.tasks" and "invoke.tasks" not in tainted
            else None
        )
        return bound, kind
    bound = alias.asname or alias.name
    exports = {
        ("invoke", "task"): ("task", "invoke.task"),
        ("invoke.tasks", "task"): ("task", "invoke.tasks.task"),
        ("invoke", "tasks"): ("tasks_module", "invoke.tasks"),
    }
    imported = exports.get((statement.module or "", alias.name))
    if statement.level or imported is None or imported[1] in tainted:
        return bound, None
    return bound, imported[0]


def _bind_import(
    statement: ast.Import | ast.ImportFrom, bindings: dict[str, str], tainted: set[str]
) -> None:
    for alias in statement.names:
        if alias.name == "*":
            bindings.clear()
            continue
        bound, kind = _import_binding(statement, alias, tainted)
        bindings.pop(bound, None)
        if kind is not None:
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
        return (
            []
            if isinstance(node.target, ast.Name)
            else list(ast.iter_child_nodes(node.target))
        )
    if isinstance(node, ast.comprehension):
        # The iteration target is local. Walrus assignments in the expressions
        # still bind in the containing scope and are visited normally.
        return [node.iter, *node.ifs]
    return list(ast.iter_child_nodes(node))


def _written_names(
    statement: ast.stmt,
    bindings: dict[str, str],
    *,
    tainted: set[str],
    module_bindings: dict[str, str] | None = None,
) -> tuple[set[str], set[str]]:
    """Over-approximate names a compound statement can replace."""
    module_bindings = bindings if module_bindings is None else module_bindings
    names: set[str] = set()
    mutations: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        export = _mutated_export(node, bindings, tainted)
        if export is not None:
            mutations.add(export)
        if isinstance(node, ast.ClassDef):
            mutations.update(_class_mutates_tasks(node, module_bindings, tainted))
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
    return names, mutations


def _deleted_names(statement: ast.stmt) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
            names.add(node.id)
    return names


def _class_mutates_tasks(
    node: ast.ClassDef, bindings: dict[str, str], tainted: set[str]
) -> set[str]:
    """Inspect executed class code without leaking its local names outward."""
    class_bindings = bindings.copy()
    class_taints = tainted.copy()
    mutations: set[str] = set()
    for statement in node.body:
        written, mutation = _written_names(
            statement, class_bindings, tainted=class_taints, module_bindings=bindings
        )
        class_taints.update(mutation)
        mutations.update(mutation)
        if "*" in written:
            class_bindings.clear()
        for bound in written:
            class_bindings.pop(bound, None)
        # Removing a class-local shadow resumes lookup in module globals.
        # Conditional deletes are treated as possible fallbacks conservatively.
        for bound in _deleted_names(statement):
            if bound in bindings:
                class_bindings[bound] = bindings[bound]
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, class_bindings, class_taints)
    return mutations


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
    tainted: set[str] = set()
    for statement in tree.body:
        # Decorators are evaluated before function defaults. Read the task
        # decorator binding first, then apply writes from definition headers.
        names = (
            _task_names(statement, bindings, tainted)
            if isinstance(statement, ast.FunctionDef)
            else []
        )
        written, mutation = _written_names(statement, bindings, tainted=tainted)
        # Re-imports reuse Python's cached modules; an import cannot restore
        # trust after the decorator export has been replaced or deleted.
        tainted.update(mutation)
        if "*" in written:
            bindings.clear()
            functions.clear()
        for bound in written:
            bindings.pop(bound, None)
            functions.pop(bound, None)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings, tainted)
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
