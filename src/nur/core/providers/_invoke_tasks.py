"""Static Invoke decorator metadata and signature validation."""

from __future__ import annotations

import ast

from nur.core.providers._invoke_control_flow import literal_exception

__all__ = [
    "bind_callable_definition",
    "constructor_exception",
    "decorator_matches",
    "deferred_failure",
    "definition_exception",
    "fatal_decorator",
    "literal_default",
    "module_kind",
    "task_definition",
    "task_names",
]

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


def decorator_matches(
    expression: ast.expr, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if isinstance(expression, ast.Name):
        return bindings.get(expression.id) == "task"
    if not isinstance(expression, ast.Attribute) or expression.attr != "task":
        return False
    kind = module_kind(expression.value, bindings, tainted)
    export = {"module": "invoke.task", "tasks_module": "invoke.tasks.task"}.get(
        kind or ""
    )
    return export is not None and export not in tainted


def module_kind(
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


def _literal_aliases(expression: ast.expr) -> list[str] | None:
    if not isinstance(expression, (ast.Tuple, ast.List)):
        return None
    aliases: list[str] = []
    for element in expression.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        aliases.append(element.value)
    return aliases


def _invalid_literal_option(
    keyword: ast.keyword, bindings: dict[str, str], tainted: set[str]
) -> bool:
    invalid_members = (
        keyword.arg in {"pre", "post"}
        and isinstance(keyword.value, ast.Name)
        and bindings.get(keyword.value.id) in {"task_object", "invalid_task"}
    ) or (
        keyword.arg in {"pre", "post"}
        and isinstance(keyword.value, (ast.List, ast.Tuple, ast.Set))
        and any(
            isinstance(element, (ast.List, ast.Tuple, ast.Set, ast.Dict))
            or _literal_dependency(element, bindings, tainted)
            for element in keyword.value.elts
        )
    )
    try:
        value = ast.literal_eval(keyword.value)
    except (ValueError, TypeError) as _exc:
        # Computed members remain unknown, but literal hook members are checked.
        return invalid_members
    iterable = isinstance(value, (str, bytes, list, tuple, dict, set))
    if keyword.arg in {"aliases", "optional", "positional"}:
        return (
            not iterable and not (keyword.arg == "positional" and value is None)
        ) or (
            keyword.arg == "aliases"
            and isinstance(value, (str, bytes, list, tuple, dict, set))
            and any(not isinstance(member, str) for member in value)
        )
    if keyword.arg in {"pre", "post"}:
        return bool(value) or invalid_members
    if keyword.arg in {"iterable", "incrementable"}:
        return bool(value) and not iterable
    if keyword.arg == "help":
        return bool(value) and not isinstance(value, dict)
    return False


def literal_default(
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> bool:
    for decorator in function.decorator_list:
        if isinstance(decorator, ast.Call):
            for keyword in decorator.keywords:
                if keyword.arg == "default":
                    try:
                        return bool(ast.literal_eval(keyword.value))
                    except (ValueError, TypeError) as _exc:
                        return False
    return False


def _literal_name(expression: ast.expr, fallback: str) -> str | None:
    if isinstance(expression, ast.Call):
        return None
    try:
        value = ast.literal_eval(expression)
    except (ValueError, TypeError) as _exc:
        return None
    if value and not isinstance(value, str):
        return None
    return value if isinstance(value, str) and value else fallback


def _literal_metadata(
    decorator: ast.expr, name: str, bindings: dict[str, str], tainted: set[str]
) -> tuple[str, list[str]] | None:
    aliases: list[str] = []
    if not isinstance(decorator, ast.Call):
        return name, aliases
    for keyword in decorator.keywords:
        if keyword.arg not in _TASK_OPTIONS or _invalid_literal_option(
            keyword, bindings, tainted
        ):
            return None
        if keyword.arg == "name":
            parsed_name = _literal_name(keyword.value, name)
            if parsed_name is None:
                return None
            name = parsed_name
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


def _literal_help_matches(
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, decorator: ast.expr
) -> bool:
    if not isinstance(decorator, ast.Call):
        return True
    help_mapping = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "help"), None
    )
    if not isinstance(help_mapping, ast.Dict):
        return True
    keys = {key.value for key in help_mapping.keys if isinstance(key, ast.Constant)}
    args = _task_arguments(function)
    if args is None:
        return True
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


def _literal_dependency(
    expression: ast.expr, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if decorator_matches(expression, bindings, tainted):
        return True
    if isinstance(expression, ast.Lambda) or (
        isinstance(expression, ast.Name)
        and bindings.get(expression.id) == "ordinary_callable"
    ):
        return True
    try:
        ast.literal_eval(expression)
    except (ValueError, TypeError) as _exc:
        return False
    return True


def _fatal_contextless_task(
    function: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.expr
) -> bool:
    args = function.args
    if any((args.posonlyargs, args.args, args.vararg, args.kwonlyargs, args.kwarg)):
        return False
    if isinstance(decorator, ast.Call):
        if any(keyword.arg is None for keyword in decorator.keywords):
            return False
        positional = next(
            (
                keyword.value
                for keyword in decorator.keywords
                if keyword.arg == "positional"
            ),
            None,
        )
        return positional is None or (
            isinstance(positional, ast.Constant) and positional.value is None
        )
    return True


def _annotation_exception(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> str | None:
    if isinstance(node, ast.ClassDef):
        return None
    annotations = [
        arg.annotation
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    ]
    annotations.extend(
        arg.annotation for arg in (node.args.vararg, node.args.kwarg) if arg is not None
    )
    annotations.append(node.returns)
    for annotation in annotations:
        if (
            annotation is not None
            and (exception := literal_exception(annotation)) is not None
        ):
            return exception
    return None


def _fatal_annotation_task(
    function: ast.FunctionDef | ast.AsyncFunctionDef, decorator: ast.expr
) -> str | None:
    exception = _annotation_exception(function)
    if isinstance(decorator, ast.Call) and any(
        keyword.arg == "positional"
        and not (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        )
        for keyword in decorator.keywords
    ):
        return None
    return exception


def _positional_pre_conflict(decorator: ast.Call, bindings: dict[str, str]) -> bool:
    if not decorator.args or not any(
        keyword.arg == "pre" for keyword in decorator.keywords
    ):
        return False
    if any(isinstance(argument, ast.Starred) for argument in decorator.args):
        return False
    if len(decorator.args) > 1:
        return True
    argument = decorator.args[0]
    if isinstance(argument, ast.Name):
        return bindings.get(argument.id) in {"task_object", "invalid_task"}
    try:
        ast.literal_eval(argument)
    except (ValueError, TypeError) as _exc:
        return False
    return True


def _fatal_help_literal(keyword: ast.keyword) -> bool:
    if keyword.arg != "help":
        return False
    try:
        value = ast.literal_eval(keyword.value)
    except (ValueError, TypeError) as _exc:
        return False
    return bool(value) and not isinstance(value, (dict, list, set))


def _fatal_callable_dispatch(
    decorator: ast.expr, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if not isinstance(decorator, ast.Call) or len(decorator.args) != 1:
        return False
    argument = decorator.args[0]
    return (
        isinstance(argument, ast.Lambda)
        or (
            isinstance(argument, ast.Name)
            and bindings.get(argument.id) == "ordinary_callable"
        )
        or decorator_matches(argument, bindings, tainted)
    )


def task_definition(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
) -> bool:
    if len(node.decorator_list) != 1:
        return False
    decorator = node.decorator_list[0]
    if isinstance(decorator, ast.Call) and any(
        keyword.arg in {"klass", None} for keyword in decorator.keywords
    ):
        return False
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    return decorator_matches(expression, bindings, tainted)


def _literal_name_failure(expression: ast.expr) -> bool:
    try:
        value = ast.literal_eval(expression)
    except (ValueError, TypeError) as _exc:
        return False
    return bool(value) and not isinstance(value, str)


def _fatal_parser_option(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    keyword: ast.keyword,
    bindings: dict[str, str],
    tainted: set[str],
) -> bool:
    if len(node.decorator_list) != 1:
        return False
    if keyword.arg == "name":
        return _literal_name_failure(keyword.value)
    if not _literal_help_matches(node, node.decorator_list[0]):
        return True
    if keyword.arg in {"aliases", "positional", "help"} and _invalid_literal_option(
        keyword, bindings, tainted
    ):
        return True
    args = _task_arguments(node)
    if keyword.arg not in {"iterable", "incrementable", "positional"} or args is None:
        return False
    parameter_count = (
        len(args.posonlyargs)
        + len(args.args)
        + len(args.kwonlyargs)
        + bool(args.vararg)
        + bool(args.kwarg)
    )
    # Invoke removes the first signature parameter as the Context. Membership
    # checks for these options occur only while constructing other arguments.
    return parameter_count > 1 and _invalid_membership_option(
        keyword, bindings, tainted
    )


def _invalid_membership_option(
    keyword: ast.keyword, bindings: dict[str, str], tainted: set[str]
) -> bool:
    if _invalid_literal_option(keyword, bindings, tainted):
        return True
    try:
        value = ast.literal_eval(keyword.value)
    except (ValueError, TypeError) as _exc:
        return False
    # Optional is normalized to a tuple in Task.__init__. These options are
    # retained as containers and Python rejects str membership in bytes.
    return isinstance(value, bytes) and (bool(value) or keyword.arg == "positional")


def _literal_constructor_failure(keyword: ast.keyword) -> bool:
    if keyword.arg != "klass":
        return False
    try:
        value = ast.literal_eval(keyword.value)
    except (ValueError, TypeError) as _exc:
        return False
    return not callable(value)


def _fatal_keyword(
    keyword: ast.keyword, bindings: dict[str, str], tainted: set[str]
) -> bool:
    return (
        (keyword.arg is not None and keyword.arg not in _TASK_OPTIONS)
        or (
            keyword.arg == "optional"
            and _invalid_literal_option(keyword, bindings, tainted)
        )
        or _fatal_help_literal(keyword)
    )


def fatal_decorator(
    function: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
    *,
    states: list[tuple[dict[str, str], set[str]]] | None = None,
) -> bool:
    for index, decorator in enumerate(function.decorator_list):
        if states is not None:
            bindings, tainted = states[index]
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not decorator_matches(expression, bindings, tainted):
            continue
        keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
        if any(_literal_constructor_failure(keyword) for keyword in keywords):
            return True
        if isinstance(decorator, ast.Call) and _positional_pre_conflict(
            decorator, bindings
        ):
            return True
        if any(keyword.arg in {"klass", None} for keyword in keywords):
            # Explicit or unpacked custom constructors may accept other options.
            continue
        if _fatal_callable_dispatch(decorator, bindings, tainted):
            return True
        if (
            isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
            and decorator is function.decorator_list[-1]
            and (
                _fatal_contextless_task(function, decorator)
                or _fatal_annotation_task(function, decorator)
            )
        ):
            return True
        if any(_fatal_keyword(keyword, bindings, tainted) for keyword in keywords):
            return True
    return False


def typeerror_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
    *,
    states: list[tuple[dict[str, str], set[str]]] | None = None,
) -> bool:
    """Recognize constructor TypeErrors, excluding later collection failures."""
    for index, decorator in enumerate(node.decorator_list):
        if states is not None:
            bindings, tainted = states[index]
        expression = decorator.func if isinstance(decorator, ast.Call) else decorator
        if not decorator_matches(expression, bindings, tainted):
            continue
        keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
        if any(_literal_constructor_failure(keyword) for keyword in keywords):
            return True
        if isinstance(decorator, ast.Call) and _positional_pre_conflict(
            decorator, bindings
        ):
            return True
        if any(keyword.arg in {"klass", None} for keyword in keywords):
            continue
        if _fatal_callable_dispatch(decorator, bindings, tainted):
            return True
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and decorator is node.decorator_list[-1]
            and _fatal_contextless_task(node, decorator)
        ):
            return True
        if any(
            (keyword.arg is not None and keyword.arg not in _TASK_OPTIONS)
            or (
                keyword.arg == "optional"
                and _invalid_literal_option(keyword, bindings, tainted)
            )
            for keyword in keywords
        ):
            return True
    return False


def _supported_signature(function: ast.FunctionDef) -> bool:
    if not (function.args.posonlyargs or function.args.args):
        return function.args.vararg is not None
    return len(function.args.posonlyargs) <= 1 and function.args.vararg is None


def task_names(
    function: ast.FunctionDef, bindings: dict[str, str], tainted: set[str]
) -> list[str]:
    # Other decorators can replace the callable/name or discard the Task object.
    if (
        len(function.decorator_list) != 1
        or not _supported_signature(function)
        or deferred_failure(function, bindings, tainted)
    ):
        return []
    decorator = function.decorator_list[0]
    expression = decorator.func if isinstance(decorator, ast.Call) else decorator
    if not decorator_matches(
        expression, bindings, tainted
    ) or not _literal_help_matches(function, decorator):
        return []
    if (
        isinstance(decorator, ast.Call)
        and decorator.args
        and (
            any(keyword.arg == "pre" for keyword in decorator.keywords)
            or any(
                _literal_dependency(argument, bindings, tainted)
                for argument in decorator.args
            )
        )
    ):
        return []
    metadata = _literal_metadata(decorator, function.name, bindings, tainted)
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


def definition_exception(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
) -> str | None:
    if isinstance(node, ast.ClassDef):
        return None
    if any(
        isinstance(decorator, ast.Call)
        and decorator_matches(decorator.func, bindings, tainted)
        and _positional_pre_conflict(decorator, bindings)
        for decorator in node.decorator_list
    ):
        return "TypeError"
    for value in [
        *node.args.defaults,
        *(value for value in node.args.kw_defaults if value is not None),
    ]:
        if (exception := literal_exception(value)) is not None:
            return exception
    return None


def constructor_exception(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
    *,
    states: list[tuple[dict[str, str], set[str]]] | None = None,
) -> str | None:
    if (exception := definition_exception(node, bindings, tainted)) is not None:
        return exception
    if typeerror_decorator(node, bindings, tainted, states=states):
        return "TypeError"
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.decorator_list
    ):
        decorator = node.decorator_list[-1]
        if task_definition(node, bindings, tainted) and (
            exception := _fatal_annotation_task(node, decorator)
        ):
            return exception
    for index, decorator in enumerate(node.decorator_list):
        if states is not None:
            bindings, tainted = states[index]
        if (
            isinstance(decorator, ast.Call)
            and decorator_matches(decorator.func, bindings, tainted)
            and not any(
                keyword.arg in {"klass", None} for keyword in decorator.keywords
            )
            and any(_fatal_help_literal(keyword) for keyword in decorator.keywords)
        ):
            return "AttributeError"
    return None


def _task_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> ast.arguments | None:
    if not isinstance(node, ast.ClassDef):
        return node.args
    if node.bases or node.keywords:
        # A custom/inherited metaclass may supply a different __call__.
        return None
    # Invoke inspects the class object's __call__, not __init__.
    return ast.arguments(
        posonlyargs=[],
        args=[],
        vararg=ast.arg(arg="args"),
        kwonlyargs=[],
        kw_defaults=[],
        kwarg=ast.arg(arg="kwargs"),
        defaults=[],
    )


def deferred_failure(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    bindings: dict[str, str],
    tainted: set[str],
) -> bool:
    if (
        not task_definition(node, bindings, tainted)
        or constructor_exception(node, bindings, tainted) is not None
    ):
        return False
    if _annotation_exception(node):
        return True
    args = _task_arguments(node)
    if args is not None and not any((
        args.posonlyargs,
        args.args,
        args.vararg,
        args.kwonlyargs,
        args.kwarg,
    )):
        return True
    decorator = node.decorator_list[0]
    return isinstance(decorator, ast.Call) and any(
        _fatal_parser_option(node, keyword, bindings, tainted)
        for keyword in decorator.keywords
    )


def bind_callable_definition(statement: ast.stmt, bindings: dict[str, str]) -> None:
    if (
        isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not statement.decorator_list
    ):
        bindings[statement.name] = "ordinary_callable"
