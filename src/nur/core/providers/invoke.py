"""Discover Invoke tasks by parsing Python, without importing project code."""

from __future__ import annotations

import ast
import logging
import tokenize
import warnings
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._invoke_exports import STAR_EXPORTS as _STAR_EXPORTS
from nur.core.providers._invoke_tasks import (
    decorator_matches as _decorator_matches,
    fatal_decorator as _fatal_decorator,
    literal_default as _literal_default,
    module_kind as _module_kind,
    task_definition as _task_definition,
    task_names as _task_names,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["InvokeProvider", "parse_tasks"]

log = logging.getLogger("nur")
_SOURCE_FILE = "tasks.py"
_ATTRIBUTE_ARGS_MIN = 2
_MAX_UNROLLED_ITERATIONS = 2
_BUILTIN_HELPERS = frozenset({"setattr", "delattr", "vars", "globals", "locals"})


# Custom klass= is excluded because it may change naming/registration semantics.


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


def _mapping_namespace(mapping: ast.expr, bindings: dict[str, str]) -> ast.expr | None:
    if isinstance(mapping, ast.Attribute) and mapping.attr == "__dict__":
        return mapping.value
    if (
        isinstance(mapping, ast.Call)
        and _builtin_name(mapping.func, bindings) == "vars"
        and len(mapping.args) == 1
    ):
        return mapping.args[0]
    return None


def _mapping_keys(call: ast.Call, method: str) -> set[str]:
    if method in {"clear", "popitem"}:
        return {"*"}
    if method in {"__setitem__", "__delitem__", "pop"} and call.args:
        key = call.args[0]
        return (
            {key.value}
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
            else {"*"}
        )
    if method != "update":
        return set()
    keys = {keyword.arg or "*" for keyword in call.keywords}
    for argument in call.args:
        if isinstance(argument, ast.Dict):
            keys.update(
                key.value
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
                else "*"
                for key in argument.keys
            )
        else:
            keys.add("*")
    return keys


def _mapping_target_write(node: ast.AST) -> tuple[ast.expr, set[str]] | None:
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, (ast.Store, ast.Del)):
        key = node.slice
        keys = (
            {key.value}
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
            else {"*"}
        )
        return node.value, keys
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.value, _mapping_keys(node, node.func.attr)
    return None


def _mapping_write(
    node: ast.AST, bindings: dict[str, str]
) -> tuple[ast.expr, set[str]] | None:
    if (write := _mapping_target_write(node)) is not None:
        mapping, keys = write
        namespace = _mapping_namespace(mapping, bindings)
        if namespace is not None:
            return namespace, keys
    return None


def _global_mapping_write(
    node: ast.AST, bindings: dict[str, str]
) -> tuple[set[str], bool]:
    if (write := _mapping_target_write(node)) is not None:
        mapping, keys = write
        if isinstance(mapping, ast.Call) and not mapping.args and not mapping.keywords:
            helper = _builtin_name(mapping.func, bindings)
            if helper in {"globals", "locals"}:
                return keys, helper == "globals"
    return set(), False


def _scope_mapping_writes(
    statement: ast.stmt, bindings: dict[str, str], *, module_scope: bool
) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    globals_: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        pending.extend(_module_children(node))
        mapping_names, global_mapping = _global_mapping_write(node, bindings)
        names.update(mapping_names)
        if global_mapping or module_scope:
            globals_.update(mapping_names)
    return names, globals_


def _mutated_exports(
    node: ast.AST, bindings: dict[str, str], tainted: set[str]
) -> set[str]:
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
        namespace, attributes = node.value, {node.attr}
    elif (
        isinstance(node, ast.Call)
        and _builtin_name(node.func, bindings) in {"setattr", "delattr"}
        and len(node.args) >= _ATTRIBUTE_ARGS_MIN
    ):
        attribute = node.args[1]
        if isinstance(attribute, ast.Constant):
            if not isinstance(attribute.value, str):
                return set()
            attributes = {attribute.value}
        else:
            attributes = {"*"}
        namespace = node.args[0]
    elif (mapping_write := _mapping_write(node, bindings)) is not None:
        namespace, attributes = mapping_write
    else:
        return set()
    kind = _module_kind(namespace, bindings, tainted)
    exports: set[str] = set()
    if kind in {"module", "ambiguous_module"}:
        if attributes & {"task", "*"}:
            exports.add("invoke.task")
        if attributes & {"tasks", "*"}:
            exports.add("invoke.tasks")
    if kind in {"tasks_module", "ambiguous_module"} and attributes & {"task", "*"}:
        exports.add("invoke.tasks.task")
    return exports


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
        and alias.name in _BUILTIN_HELPERS
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
    children: list[ast.AST] = list(ast.iter_child_nodes(node))
    if isinstance(node, ast.If):
        truth = _constant_truth(node.test)
        if truth is not None:
            children = [node.test, *(node.body if truth else node.orelse)]
    return children


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
        mutations.update(_mutated_exports(node, bindings, tainted))
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


def _all_statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
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


def _statement_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, ast.If):
        truth = _constant_truth(statement.test)
        if truth is not None:
            return [statement.body if truth else statement.orelse]
    return _all_statement_blocks(statement)


def _global_names(statements: list[ast.stmt]) -> set[str]:
    names: set[str] = set()
    for statement in statements:
        if isinstance(statement, ast.Global):
            names.update(statement.names)
        for block in _all_statement_blocks(statement):
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
                "globals",
                "locals",
            }:
                previous = bindings.get(bound)
                bindings[bound] = (
                    "ambiguous_module" if previous and previous != kind else kind
                )


def _unpacked_pairs(
    target: ast.Tuple | ast.List, value: ast.Tuple | ast.List
) -> list[tuple[ast.expr, ast.expr]]:
    if any(isinstance(item, ast.Starred) for item in value.elts):
        return []
    starred = next(
        (
            index
            for index, item in enumerate(target.elts)
            if isinstance(item, ast.Starred)
        ),
        None,
    )
    if starred is None:
        return (
            list(zip(target.elts, value.elts, strict=True))
            if len(target.elts) == len(value.elts)
            else []
        )
    if len(value.elts) < len(target.elts) - 1:
        return []
    suffix = len(target.elts) - starred - 1
    pairs = list(zip(target.elts[:starred], value.elts[:starred], strict=True))
    if suffix:
        pairs.extend(zip(target.elts[-suffix:], value.elts[-suffix:], strict=True))
    return pairs


def _constant_truth(expression: ast.expr) -> bool | None:
    try:
        return bool(ast.literal_eval(expression))
    except (ValueError, TypeError) as _exc:
        return None


def _certain_children(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BoolOp):
        children: list[ast.AST] = []
        for value in node.values:
            children.append(value)
            truth = _constant_truth(value)
            if truth is None or truth == isinstance(node.op, ast.Or):
                break
        return children
    if isinstance(node, ast.IfExp):
        truth = _constant_truth(node.test)
        return (
            [node.test]
            if truth is None
            else [node.test, node.body if truth else node.orelse]
        )
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        return [node.generators[0].iter]
    if isinstance(node, ast.Compare):
        return [node.left, node.comparators[0]]
    return _module_children(node)


def _assignment_pairs(
    statement: ast.stmt, *, certain: bool = True
) -> list[tuple[ast.Name, ast.expr]]:
    pending: list[tuple[ast.expr, ast.expr]]
    if isinstance(statement, ast.Assign):
        pending = [(target, statement.value) for target in statement.targets]
    elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
        pending = [(statement.target, statement.value)]
    else:
        pending = []
    nodes: list[ast.AST] = [statement]
    while nodes:
        node = nodes.pop()
        nodes.extend(
            child
            for child in (
                _certain_children(node) if certain else _module_children(node)
            )
            if not isinstance(child, ast.stmt)
        )
        if isinstance(node, ast.NamedExpr):
            pending.append((node.target, node.value))
    pairs: list[tuple[ast.Name, ast.expr]] = []
    while pending:
        target, value = pending.pop()
        if isinstance(target, ast.Name):
            pairs.append((target, value))
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(
            value, (ast.Tuple, ast.List)
        ):
            pending.extend(_unpacked_pairs(target, value))
    return pairs


def _copied_modules(
    statement: ast.stmt, bindings: dict[str, str], tainted: set[str]
) -> dict[str, str]:
    copies: dict[str, str] = {}
    for target, value in _assignment_pairs(statement, certain=False):
        kind = _module_kind(value, bindings, tainted)
        if isinstance(value, ast.Name) and bindings.get(value.id) == "builtins":
            kind = "builtins"
        kind = kind or _builtin_name(value, bindings)
        if kind is not None:
            copies[target.id] = kind
    return copies


def _copied_callables(
    statement: ast.stmt, bindings: dict[str, str], tainted: set[str]
) -> dict[str, str]:
    copies: dict[str, str] = {}
    for target, value in _assignment_pairs(statement):
        if isinstance(value, ast.Lambda):
            copies[target.id] = "ordinary_callable"
        elif isinstance(value, ast.Name) and bindings.get(value.id) in {
            "ordinary_callable",
            "task_object",
        }:
            copies[target.id] = bindings[value.id]
        elif _decorator_matches(value, bindings, tainted):
            copies[target.id] = "task"
    return copies


def _defined_task_bindings(
    statement: ast.stmt, bindings: dict[str, str], tainted: set[str]
) -> dict[str, str]:
    if isinstance(
        statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    ) and _task_definition(statement, bindings, tainted):
        return {statement.name: "task_object"}
    return {}


def _defined_defaults(statement: ast.stmt, copies: dict[str, str]) -> set[str]:
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and copies.get(statement.name) == "task_object"
        and _literal_default(statement)
    ):
        return {statement.name}
    return set()


def _bind_callable_definition(statement: ast.stmt, bindings: dict[str, str]) -> None:
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not statement.decorator_list
    ):
        bindings[statement.name] = "ordinary_callable"


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
        mapping_writes = _scope_mapping_writes(
            statement, class_bindings, module_scope=module_bindings is None
        )
        written.update(mapping_writes[0])
        global_writes.update(mapping_writes[1] | nested_writes | (written & globals_))
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
    return {
        target.id
        for target, value in _assignment_pairs(statement)
        if isinstance(value, ast.Name) and value.id in defaults
    }


def _copied_tasks(
    statement: ast.stmt, functions: dict[str, list[Task]]
) -> dict[str, list[Task]]:
    return {
        target.id: functions[value.id]
        for target, value in _assignment_pairs(statement)
        if isinstance(value, ast.Name) and value.id in functions
    }


def _loop_count(statement: ast.For | ast.AsyncFor | ast.While) -> int | None:
    if any(
        node is not statement
        and isinstance(
            node, (ast.For, ast.AsyncFor, ast.While, ast.Break, ast.Continue)
        )
        for node in ast.walk(statement)
    ):
        return None
    if isinstance(statement, ast.While):
        return (
            0
            if isinstance(statement.test, ast.Constant) and not statement.test.value
            else None
        )
    if isinstance(statement.iter, (ast.List, ast.Tuple)) and not any(
        isinstance(item, ast.Starred) for item in statement.iter.elts
    ):
        return len(statement.iter.elts)
    return None


def _loop_task_blocks(
    statement: ast.For | ast.AsyncFor | ast.While,
) -> list[list[ast.stmt]]:
    count = _loop_count(statement)
    if count is not None and count <= _MAX_UNROLLED_ITERATIONS:
        return [statement.body * count + statement.orelse]
    # Inspect possible copies for default collisions, but survival of written
    # task bindings is handled conservatively for arbitrary iteration counts.
    return [statement.body + statement.orelse, statement.orelse]


def _task_outcome_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, ast.If):
        if isinstance(statement.test, ast.Constant):
            return [statement.body if statement.test.value else statement.orelse]
        return [statement.body, statement.orelse]
    if isinstance(statement, ast.Match):
        blocks = [case.body for case in statement.cases]
        if not any(
            isinstance(case.pattern, ast.MatchAs)
            and case.pattern.pattern is None
            and case.guard is None
            for case in statement.cases
        ):
            blocks.append([])
        return blocks
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return _loop_task_blocks(statement)
    if isinstance(statement, (ast.Try, ast.TryStar)):
        return [
            statement.body + statement.orelse,
            *(handler.body for handler in statement.handlers),
        ]
    return _statement_blocks(statement)


def _header_writes(statement: ast.stmt) -> set[str]:
    names: set[str] = set()
    pending: list[ast.AST] = [statement]
    while pending:
        node = pending.pop()
        children = _module_children(node)
        if isinstance(node, (ast.For, ast.AsyncFor)) and _loop_count(node) == 0:
            children = [child for child in children if child is not node.target]
        pending.extend(child for child in children if not isinstance(child, ast.stmt))
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif (
            isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar))
            and node.name
        ):
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names


def _task_binding_effects(
    statements: list[ast.stmt], functions: dict[str, list[Task]], defaults: set[str]
) -> tuple[dict[str, list[Task]], set[str]]:
    functions, defaults = functions.copy(), defaults.copy()
    for statement in statements:
        copies = _copied_tasks(statement, functions)
        default_copies = _copied_defaults(statement, defaults)
        for bound in _header_writes(statement):
            functions.pop(bound, None)
            defaults.discard(bound)
        blocks = _task_outcome_blocks(statement)
        branches = [
            _task_binding_effects(block, functions, defaults) for block in blocks
        ]
        written, _, _ = _written_names(
            statement, {}, tainted=set(), inspect_classes=False
        )
        if "*" in written:
            functions.clear()
            defaults.clear()
        for bound in written:
            functions.pop(bound, None)
            defaults.discard(bound)
        functions.update(copies)
        defaults.update(default_copies)
        if branches:
            functions.update({
                bound: tasks
                for bound, tasks in branches[0][0].items()
                if all(
                    branch_functions.get(bound) == tasks
                    for branch_functions, _ in branches
                )
            })
            for _, branch_defaults in branches:
                defaults.update(branch_defaults)
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)) and (
            _loop_count(statement) is None
            or (_loop_count(statement) or 0) > _MAX_UNROLLED_ITERATIONS
        ):
            for bound in written:
                functions.pop(bound, None)
        if isinstance(statement, (ast.Try, ast.TryStar)):
            functions, defaults = _task_binding_effects(
                statement.finalbody, functions, defaults
            )
    return functions, defaults


def _loop_must_enter(statement: ast.For | ast.AsyncFor | ast.While) -> bool:
    if isinstance(statement, ast.While):
        return _constant_truth(statement.test) is True
    if isinstance(statement.iter, (ast.List, ast.Tuple, ast.Set)):
        return bool(statement.iter.elts) and not any(
            isinstance(item, ast.Starred) for item in statement.iter.elts
        )
    if isinstance(statement.iter, ast.Dict):
        return any(key is not None for key in statement.iter.keys)
    if isinstance(statement.iter, ast.Constant) and isinstance(
        statement.iter.value, (str, bytes)
    ):
        return bool(statement.iter.value)
    return False


def _iteration_jump(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.Break, ast.Continue)):
        return True
    if isinstance(statement, ast.If):
        return any(
            _iteration_jump(child)
            for block in _statement_blocks(statement)
            for child in block
        )
    return False


def _definitely_executed_blocks(statement: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(statement, ast.If):
        truth = _constant_truth(statement.test)
        return [] if truth is None else [statement.body if truth else statement.orelse]
    if isinstance(statement, ast.ClassDef):
        return [statement.body]
    if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
        return [statement.body] if _loop_must_enter(statement) else []
    return []


def _fatal_children(
    statement: ast.stmt, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if isinstance(statement, (ast.Try, ast.TryStar)):
        bindings, tainted = bindings.copy(), tainted.copy()
        for child in [
            *statement.body,
            *statement.orelse,
            *(child for handler in statement.handlers for child in handler.body),
        ]:
            written, mutation, _ = _written_names(
                child, bindings, tainted=tainted, inspect_classes=False
            )
            tainted.update(mutation)
            for bound in written:
                bindings.pop(bound, None)
        return _fatal_block(statement.finalbody, bindings, tainted)
    return any(
        _fatal_block(block, bindings, tainted)
        for block in _definitely_executed_blocks(statement)
    )


def _fatal_block(
    statements: list[ast.stmt], bindings: dict[str, str], tainted: set[str]
) -> bool:
    bindings, tainted = bindings.copy(), tainted.copy()
    for statement in statements:
        if isinstance(
            statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ) and _fatal_decorator(statement, bindings, tainted):
            return True
        if _fatal_children(statement, bindings, tainted):
            return True
        if _iteration_jump(statement):
            break
        task_object = isinstance(statement, ast.FunctionDef) and bool(
            _task_names(statement, bindings, tainted)
        )
        copies = _copied_modules(statement, bindings, tainted)
        copies.update(_copied_callables(statement, bindings, tainted))
        copies.update(_defined_task_bindings(statement, bindings, tainted))
        written, mutation, _ = _written_names(
            statement, bindings, tainted=tainted, inspect_classes=False
        )
        tainted.update(mutation)
        if "*" in written:
            bindings.clear()
        for bound in written:
            bindings.pop(bound, None)
        bindings.update(copies)
        _bind_callable_definition(statement, bindings)
        if task_object and isinstance(statement, ast.FunctionDef):
            bindings[statement.name] = "task_object"
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings, tainted)
    return False


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
        if _fatal_block([statement], bindings, tainted):
            log.warning("nur: skipping %s (fatal Invoke decorator)", source_file)
            return []
        # Decorators are evaluated before function defaults. Read the task
        # decorator binding first, then apply writes from definition headers.
        names = (
            _task_names(statement, bindings, tainted)
            if isinstance(statement, ast.FunctionDef)
            else []
        )
        copied_callables = _copied_callables(statement, bindings, tainted)
        copied_callables.update(_defined_task_bindings(statement, bindings, tainted))
        copied_tasks, copied_defaults = _task_binding_effects(
            [statement], functions, defaults
        )
        copied_defaults.update(_defined_defaults(statement, copied_callables))
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
        bindings.update(copied_callables)
        defaults.update(copied_defaults - global_writes)
        functions.update({
            bound: tasks
            for bound, tasks in copied_tasks.items()
            if bound not in global_writes
        })
        _bind_callable_definition(statement, bindings)
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            _bind_import(statement, bindings, tainted)
        elif isinstance(statement, ast.FunctionDef):
            docstring = ast.get_docstring(statement)
            functions[statement.name] = [
                Task(
                    name=name,
                    prefix="invoke",
                    argv_base=("invoke", name),
                    description=docstring.splitlines()[0] if docstring else None,
                    source_file=source_file,
                )
                for name in names
            ]
        bindings.update({
            bound: "task_object" for bound, tasks in functions.items() if tasks
        })
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
