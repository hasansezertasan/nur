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
# Public bindings of the supported Invoke modules. Star imports replace these
# names, while unrelated project functions and aliases remain bound.
_STAR_EXPORTS = {
    "invoke": frozenset([
        "metadata",
        "Any",
        "Collection",
        "Config",
        "Context",
        "MockContext",
        "AmbiguousEnvVar",
        "AuthFailure",
        "CollectionNotFound",
        "CommandTimedOut",
        "Exit",
        "ParseError",
        "PlatformError",
        "ResponseNotAccepted",
        "SubprocessPipeError",
        "ThreadException",
        "UncastableEnvVar",
        "UnexpectedExit",
        "UnknownFileType",
        "UnpicklableConfigMember",
        "WatcherError",
        "Executor",
        "FilesystemLoader",
        "Argument",
        "Parser",
        "ParserContext",
        "ParseResult",
        "Program",
        "Failure",
        "Local",
        "Promise",
        "Result",
        "Runner",
        "Call",
        "Task",
        "call",
        "task",
        "pty_size",
        "FailingResponder",
        "Responder",
        "StreamWatcher",
        "run",
        "sudo",
        "collection",
        "config",
        "context",
        "exceptions",
        "executor",
        "loader",
        "parser",
        "program",
        "runners",
        "tasks",
        "terminals",
        "watchers",
        "util",
        "env",
        "completion",
        "vendor",
    ]),
    "invoke.tasks": frozenset([
        "inspect",
        "types",
        "deepcopy",
        "update_wrapper",
        "TYPE_CHECKING",
        "Any",
        "Callable",
        "Dict",
        "Generic",
        "Iterable",
        "List",
        "Optional",
        "Set",
        "Tuple",
        "Type",
        "TypeVar",
        "Union",
        "Context",
        "Argument",
        "ParseResult",
        "translate_underscores",
        "T",
        "Task",
        "task",
        "Call",
        "call",
    ]),
}
_BUILTIN_HELPERS = frozenset({"setattr", "delattr", "vars"})


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
        return kind if kind in {"module", "tasks_module", "ambiguous_module"} else None
    if (
        isinstance(namespace, ast.Attribute)
        and namespace.attr == "tasks"
        and isinstance(namespace.value, ast.Name)
        and bindings.get(namespace.value.id) == "module"
        and "invoke.tasks" not in tainted
    ):
        return "tasks_module"
    return None


def _builtin_name(expression: ast.expr, bindings: dict[str, str]) -> str | None:
    if isinstance(expression, ast.Name):
        kind = bindings.get(expression.id, expression.id)
        return kind if kind in _BUILTIN_HELPERS else None
    if (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and bindings.get(expression.value.id) == "builtins"
        and expression.attr in _BUILTIN_HELPERS
    ):
        return expression.attr
    return None


def _mapping_write(
    node: ast.AST, bindings: dict[str, str]
) -> tuple[ast.expr, str] | None:
    if not isinstance(node, ast.Subscript) or not isinstance(
        node.ctx, (ast.Store, ast.Del)
    ):
        return None
    if not isinstance(node.slice, ast.Constant) or not isinstance(
        node.slice.value, str
    ):
        return None
    mapping = node.value
    if isinstance(mapping, ast.Attribute) and mapping.attr == "__dict__":
        return mapping.value, node.slice.value
    if (
        isinstance(mapping, ast.Call)
        and _builtin_name(mapping.func, bindings) == "vars"
        and len(mapping.args) == 1
    ):
        return mapping.args[0], node.slice.value
    return None


def _mutated_export(
    node: ast.AST, bindings: dict[str, str], tainted: set[str]
) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        namespace, attribute = node.value, node.attr
    elif (
        isinstance(node, ast.Call)
        and _builtin_name(node.func, bindings) in {"setattr", "delattr"}
        and len(node.args) >= _ATTRIBUTE_ARGS_MIN
        and isinstance(node.args[1], ast.Constant)
    ):
        value = node.args[1].value
        if not isinstance(value, str):
            return None
        namespace, attribute = node.args[0], value
    elif (mapping_write := _mapping_write(node, bindings)) is not None:
        namespace, attribute = mapping_write
    else:
        return None
    kind = _module_kind(namespace, bindings, tainted)
    return {
        ("module", "task"): "invoke.task",
        ("module", "tasks"): "invoke.tasks",
        ("tasks_module", "task"): "invoke.tasks.task",
        ("ambiguous_module", "task"): "invoke.*",
        ("ambiguous_module", "tasks"): "invoke.tasks",
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


def _invalid_literal_option(keyword: ast.keyword) -> bool:
    try:
        value = ast.literal_eval(keyword.value)
    except (ValueError, TypeError) as _exc:
        # Computed option values are not evaluated by discovery.
        return False
    iterable = isinstance(value, (str, bytes, list, tuple, dict, set))
    if keyword.arg in {"optional", "positional"}:
        return not iterable and not (keyword.arg == "positional" and value is None)
    if keyword.arg in {"pre", "post"}:
        return bool(value)
    if keyword.arg in {"iterable", "incrementable"}:
        return bool(value) and not iterable
    if keyword.arg == "help":
        return bool(value) and not isinstance(value, dict)
    return False


def _literal_default(function: ast.FunctionDef) -> bool:
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "default":
                    try:
                        return bool(ast.literal_eval(keyword.value))
                    except (ValueError, TypeError) as _exc:
                        return False
    return False


def _literal_metadata(decorator: ast.expr, name: str) -> tuple[str, list[str]] | None:
    aliases: list[str] = []
    if not isinstance(decorator, ast.Call):
        return name, aliases
    for keyword in decorator.keywords:
        if keyword.arg not in _TASK_OPTIONS or _invalid_literal_option(keyword):
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


def _literal_help_matches(function: ast.FunctionDef, decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return True
    help_mapping = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "help"), None
    )
    if not isinstance(help_mapping, ast.Dict):
        return True
    if any(not isinstance(key, ast.Constant) for key in help_mapping.keys):
        # Computed keys and unpacked mappings remain outside static validation.
        return True
    keys = {key.value for key in help_mapping.keys if isinstance(key, ast.Constant)}
    args = function.args
    parameters = [*args.posonlyargs, *args.args]
    if args.vararg:
        parameters.append(args.vararg)
    parameters.extend(args.kwonlyargs)
    if args.kwarg:
        parameters.append(args.kwarg)
    for parameter in parameters[1:]:
        name = parameter.arg
        dashed = name.strip("_").replace("_", "-") if "_" in name else name
        # Invoke consumes the dashed key first, then the original spelling.
        for candidate in (dashed, name):
            if candidate in keys:
                keys.remove(candidate)
                break
    return not keys


def _literal_dependency(expression: ast.expr) -> bool:
    try:
        ast.literal_eval(expression)
    except (ValueError, TypeError) as _exc:
        return False
    return True


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
    if not _decorator_matches(
        expression, bindings, tainted
    ) or not _literal_help_matches(function, decorator):
        return []
    if (
        isinstance(decorator, ast.Call)
        and decorator.args
        and (
            any(keyword.arg == "pre" for keyword in decorator.keywords)
            or any(_literal_dependency(argument) for argument in decorator.args)
        )
    ):
        return []
    metadata = _literal_metadata(decorator, function.name)
    if metadata is None:
        return []
    name, aliases = metadata
    if "." in name or name.startswith("-"):
        return []
    # Invoke's default Collection turns underscores into dashes. Config and
    # explicit Collection wiring are outside this single-module subset.
    return list(
        dict.fromkeys(
            _normalize_name(item)
            for item in [name, *aliases]
            if item and "." not in item and not item.startswith("-")
        )
    )


def _import_binding(
    statement: ast.Import | ast.ImportFrom, alias: ast.alias, tainted: set[str]
) -> tuple[str, str | None]:
    if isinstance(statement, ast.Import):
        bound = alias.asname or alias.name.split(".")[0]
        if alias.name == "builtins":
            return bound, "builtins"
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
    if (
        statement.module == "builtins"
        and not statement.level
        and alias.name in {"setattr", "delattr", "vars"}
    ):
        return bound, alias.name
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
            exported_names = (
                _STAR_EXPORTS.get(statement.module or "")
                if isinstance(statement, ast.ImportFrom) and not statement.level
                else None
            )
            if exported_names is None:
                bindings.clear()
            else:
                for name in exported_names:
                    bindings.pop(name, None)
            if (
                isinstance(statement, ast.ImportFrom)
                and not statement.level
                and statement.module in {"invoke", "invoke.tasks"}
            ):
                exported = (
                    ["task", "tasks"] if statement.module == "invoke" else ["task"]
                )
                for name in exported:
                    bound, kind = _import_binding(
                        statement, ast.alias(name=name), tainted
                    )
                    if kind is not None:
                        bindings[bound] = kind
            continue
        bound, kind = _import_binding(statement, alias, tainted)
        bindings.pop(bound, None)
        if kind is not None:
            bindings[bound] = kind
        elif bound in _BUILTIN_HELPERS:
            bindings[bound] = "shadowed"


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


def _import_writes(node: ast.Import | ast.ImportFrom) -> set[str]:
    if (
        isinstance(node, ast.ImportFrom)
        and not node.level
        and any(alias.name == "*" for alias in node.names)
        and node.module in _STAR_EXPORTS
    ):
        return set(_STAR_EXPORTS[node.module])
    return {alias.asname or alias.name.split(".")[0] for alias in node.names}


def _written_names(
    statement: ast.stmt,
    bindings: dict[str, str],
    *,
    tainted: set[str],
    module_bindings: dict[str, str] | None = None,
    inspect_classes: bool = True,
) -> tuple[set[str], set[str], set[str]]:
    """Over-approximate names a compound statement can replace."""
    module_bindings = bindings if module_bindings is None else module_bindings
    names: set[str] = set()
    mutations: set[str] = set()
    global_writes: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        export = _mutated_export(node, bindings, tainted)
        if export == "invoke.*":
            mutations.update({"invoke.task", "invoke.tasks.task"})
        elif export is not None:
            mutations.add(export)
        if inspect_classes and isinstance(node, ast.ClassDef):
            class_writes, class_mutations = _class_mutates_tasks(
                node, module_bindings, tainted
            )
            names.update(class_writes)
            global_writes.update(class_writes)
            mutations.update(class_mutations)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(_import_writes(node))
        elif (
            isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names, mutations, global_writes


def _deleted_names(statement: ast.stmt) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del):
            names.add(node.id)
    return names


def _statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    """Get compound blocks without crossing function or class scopes."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    blocks: list[list[ast.stmt]] = []
    for _, value in ast.iter_fields(statement):
        if isinstance(value, list) and value:
            if all(isinstance(item, ast.stmt) for item in value):
                blocks.append(value)
            elif all(
                isinstance(item, (ast.ExceptHandler, ast.match_case)) for item in value
            ):
                blocks.extend(item.body for item in value)
    return blocks


def _global_names(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, ast.Global):
            names.update(statement.names)
        for block in _statement_blocks(statement):
            names.update(_global_names(block))
    return names


def _merge_possible_modules(
    bindings: dict[str, str], possibilities: list[dict[str, str]]
) -> None:
    for possible in possibilities:
        for bound, kind in possible.items():
            if kind in {
                "module",
                "tasks_module",
                "ambiguous_module",
                "builtins",
                "setattr",
                "delattr",
                "vars",
            }:
                previous = bindings.get(bound)
                bindings[bound] = (
                    "ambiguous_module" if previous and previous != kind else kind
                )


def _copied_modules(
    statement: ast.stmt, bindings: dict[str, str], tainted: set[str]
) -> dict[str, str]:
    if isinstance(statement, ast.Assign):
        targets, value = statement.targets, statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets, value = [statement.target], statement.value
    else:
        return {}
    kind = _module_kind(value, bindings, tainted)
    if isinstance(value, ast.Name) and bindings.get(value.id) == "builtins":
        kind = "builtins"
    kind = kind or _builtin_name(value, bindings)
    if kind is None:
        return {}
    return {target.id: kind for target in targets if isinstance(target, ast.Name)}


def _forget_class_bindings(
    statement: ast.stmt,
    bindings: dict[str, str],
    module_bindings: dict[str, str] | None,
    globals_: set[str],
    written: set[str],
) -> None:
    if "*" in written:
        bindings.clear()
    deleted = _deleted_names(statement)
    for bound in written:
        bindings.pop(bound, None)
        if bound in _BUILTIN_HELPERS and bound not in deleted:
            bindings[bound] = "shadowed"
    # Removing a class-local shadow resumes lookup in module globals.
    for bound in deleted - globals_:
        if module_bindings is not None and bound in module_bindings:
            bindings[bound] = module_bindings[bound]


def _class_block_effects(
    statements: list[ast.stmt],
    bindings: dict[str, str],
    module_bindings: dict[str, str] | None,
    tainted: set[str],
    globals_: set[str],
) -> tuple[set[str], set[str], dict[str, str]]:
    class_bindings = bindings.copy()
    class_taints = tainted.copy()
    global_writes: set[str] = set()
    mutations: set[str] = set()
    for statement in statements:
        # Inspect each possible block in order with its own local environment.
        # Imports within a branch are visible to subsequent mutations there.
        copied_modules = _copied_modules(statement, class_bindings, class_taints)
        branch_bindings: list[dict[str, str]] = []
        for block in _statement_blocks(statement):
            writes, effects, possible = _class_block_effects(
                block, class_bindings, module_bindings, class_taints, globals_
            )
            global_writes.update(writes)
            mutations.update(effects)
            branch_bindings.append(possible)
        written, mutation, nested_writes = _written_names(
            statement,
            class_bindings,
            tainted=class_taints,
            module_bindings=(
                class_bindings if module_bindings is None else module_bindings
            ),
            inspect_classes=not bool(_statement_blocks(statement)),
        )
        global_writes.update(nested_writes | (written & globals_))
        class_taints.update(mutation)
        mutations.update(mutation)
        _forget_class_bindings(
            statement,
            class_bindings,
            module_bindings,
            globals_,
            written | nested_writes,
        )
        # A branch may leave an imported alias in class scope. Retain possible
        # module references for conservative mutation detection after the block.
        _merge_possible_modules(class_bindings, branch_bindings)
        class_bindings.update(copied_modules)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, class_bindings, class_taints)
    return global_writes, mutations, class_bindings


def _class_mutates_tasks(
    node: ast.ClassDef, bindings: dict[str, str], tainted: set[str]
) -> tuple[set[str], set[str]]:
    """Inspect executed class code and propagate global writes outward."""
    globals_ = _global_names(node.body)
    writes, mutations, _ = _class_block_effects(
        node.body, bindings, bindings, tainted, globals_
    )
    return writes, mutations


def _copied_defaults(statement: ast.stmt, defaults: set[str]) -> set[str]:
    if isinstance(statement, ast.Assign):
        targets, value = statement.targets, statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets, value = [statement.target], statement.value
    else:
        return set()
    if not isinstance(value, ast.Name) or value.id not in defaults:
        return set()
    return {target.id for target in targets if isinstance(target, ast.Name)}


def _copied_tasks(
    statement: ast.stmt, functions: dict[str, list[Task]]
) -> dict[str, list[Task]]:
    if isinstance(statement, ast.Assign):
        targets, value = statement.targets, statement.value
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        targets, value = [statement.target], statement.value
    else:
        return {}
    if not isinstance(value, ast.Name) or value.id not in functions:
        return {}
    return {
        target.id: functions[value.id]
        for target in targets
        if isinstance(target, ast.Name)
    }


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
    defaults: set[str] = set()
    tainted: set[str] = set()
    possible_bindings: dict[str, str] = {}
    for statement in tree.body:
        # Decorators are evaluated before function defaults. Read the task
        # decorator binding first, then apply writes from definition headers.
        names = (
            _task_names(statement, bindings, tainted)
            if isinstance(statement, ast.FunctionDef)
            else []
        )
        copied_defaults = _copied_defaults(statement, defaults)
        copied_tasks = _copied_tasks(statement, functions)
        global_writes, mutation, possible_bindings = _class_block_effects(
            [statement], possible_bindings, None, tainted, set()
        )
        written, _, _ = _written_names(
            statement, bindings, tainted=tainted, inspect_classes=False
        )
        written.update(global_writes)
        # Re-imports reuse Python's cached modules; an import cannot restore
        # trust after the decorator export has been replaced or deleted.
        tainted.update(mutation)
        if "*" in written:
            bindings.clear()
            functions.clear()
            defaults.clear()
        for bound in written:
            bindings.pop(bound, None)
            functions.pop(bound, None)
            defaults.discard(bound)
        defaults.update(copied_defaults)
        functions.update(copied_tasks)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings, tainted)
        elif isinstance(statement, ast.FunctionDef):
            if names and _literal_default(statement):
                defaults.add(statement.name)
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
    if len(defaults) > 1:
        log.warning("nur: skipping %s (colliding Invoke default tasks)", source_file)
        return []
    return list(
        {task.name: task for group in functions.values() for task in group}.values()
    )


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
