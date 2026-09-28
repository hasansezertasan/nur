from __future__ import annotations

import ast
import builtins
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
# Builtins that expose, edit, or run code in a namespace, so may rebind any
# name or replace `nox.session`.
_MUTATORS = frozenset({
    "vars",
    "globals",
    "locals",
    "exec",
    "eval",
    "setattr",
    "delattr",
})
# Statements that run straight through and whose only effect on nox bindings
# is the names they store. (`global`/`nonlocal` names are never trusted.)
_PLAIN = (
    ast.Assign
    | ast.AnnAssign
    | ast.AugAssign
    | ast.Delete
    | ast.Expr
    | ast.Pass
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.ClassDef
    | ast.Global
    | ast.Nonlocal
)


class _Kind(enum.Enum):
    """What a tracked name is bound to.

    Bindings map names to a kind; a name missing from the mapping is either
    unbound or bound to something other than nox.
    """

    MODULE = enum.auto()  # a nox module: `<name>.session` is the decorator
    SESSION = enum.auto()  # the `session` decorator itself


def _mutates_namespace(tree: ast.Module) -> bool:
    """Return True if the file may replace ``nox.session`` or rebind names opaquely.

    Aliases share one module object and a re-import returns the same mutated
    module, so once ``.session`` may have been swapped (``nox.session = ...``,
    ``del nox.session``, ``setattr``/``delattr``) no decorator can be trusted.
    The same goes for a namespace edited as a mapping (``__dict__``,
    ``vars()``, ``globals()``, ``locals()``) or by running code in it
    (``exec``, ``eval``), including qualified or imported forms such as
    ``builtins.setattr``. Other attributes
    (``nox.options``, ``nox.needs_version``) do not count.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            stored = isinstance(node.ctx, ast.Store | ast.Del)
            if (node.attr == "session" and stored) or node.attr == "__dict__":
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else None
            if isinstance(func, ast.Attribute):
                name = func.attr  # e.g. `builtins.setattr(...)`
            if name in _MUTATORS:
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            if any(alias.name in _MUTATORS | {"*"} for alias in node.names):
                return True  # `from builtins import setattr as s` hides the call.
    return False


def _stores(*nodes: ast.AST | None) -> set[str]:
    """Return every name *nodes* may bind or delete.

    Conservative: stores inside comprehensions or lambdas count too, which can
    only invalidate a binding, never invent one.
    """
    names: set[str] = set()
    for node in nodes:
        if node is None:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(
                child.ctx, ast.Store | ast.Del
            ):
                names.add(child.id)
    return names


def _statement_stores(node: ast.stmt) -> set[str]:
    """Return the module-level names a plain statement may bind or delete.

    A ``def`` or ``class`` binds its name and evaluates its decorators and
    header here, but its body is its own scope.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        return {node.name} | _stores(*node.decorator_list, node.args, node.returns)
    if isinstance(node, ast.ClassDef):
        return {node.name} | _stores(*node.decorator_list, *node.bases, *node.keywords)
    return _stores(node)


def _without(bound: dict[str, _Kind], names: set[str]) -> dict[str, _Kind]:
    return {name: kind for name, kind in bound.items() if name not in names}


def _merge(paths: list[dict[str, _Kind]]) -> dict[str, _Kind]:
    """Keep only the bindings every path agrees on."""
    first, *rest = paths
    return {
        name: kind
        for name, kind in first.items()
        if all(path.get(name) == kind for path in rest)
    }


def _unknown_star_import(node: ast.stmt) -> bool:
    """Return True for ``from x import *`` of a module other than nox."""
    return (
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        and not (node.module in _NOX_MODULES and not node.level)
    )


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
            result["session"] = _Kind.SESSION  # Only nox star imports get here.
            continue
        name = alias.asname or alias.name
        if from_nox and alias.name == "session":
            result[name] = _Kind.SESSION
        else:
            result.pop(name, None)
    return result


class _Unpredictable(Exception):  # noqa: N818  # control flow, not an error
    """The noxfile uses a construct whose effect on nox's registry is unknown."""


def _never_runs(node: ast.If, *, type_checking: bool, main_name: bool) -> bool:
    """Return True for an ``if`` whose body never runs when nox imports the file.

    That is ``if TYPE_CHECKING:`` or ``if __name__ == "__main__":`` with no
    ``else`` (nox imports the noxfile under another name). *type_checking*
    and *main_name* say the file never rebinds those names.
    """
    if node.orelse:
        return False
    test = node.test
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return type_checking
    if not (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
    ):
        return False
    sides = [test.left, test.comparators[0]]
    names = {side.id for side in sides if isinstance(side, ast.Name)}
    values = {side.value for side in sides if isinstance(side, ast.Constant)}
    return main_name and names == {"__name__"} and values == {"__main__"}


def _imports_type_checking(node: ast.Import | ast.ImportFrom) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module in {"typing", "typing_extensions"}
        and not node.level
        and any(
            alias.name == "TYPE_CHECKING" and alias.asname in {None, "TYPE_CHECKING"}
            for alias in node.names
        )
    )


# Names a module has without binding them.
_IMPLICIT = frozenset(dir(builtins)) | {
    "__name__",
    "__file__",
    "__doc__",
    "__spec__",
    "__loader__",
    "__package__",
    "__builtins__",
    "__annotations__",
}


def _reads_unbound_name(tree: ast.Module) -> bool:
    """Return True if import-time code reads a name the file never binds.

    Such a read raises ``NameError`` (unless a star import supplies the name),
    so the import fails.
    """
    if any(
        isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
        for node in ast.walk(tree)
    ):
        return False
    return not _import_time_reads(tree) <= _bound_anywhere(tree) | _IMPLICIT


def _bound_anywhere(tree: ast.Module) -> set[str]:
    """Return every name the file binds, in any scope."""
    names = _stores(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            names |= {a.asname or a.name.partition(".")[0] for a in node.names}
        elif (name := _defined_name(node)) is not None:
            names.add(name)
    return names


def _defined_name(node: ast.AST) -> str | None:
    """Return the name a ``def``, ``class``, or ``except ... as`` binds."""
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name
    return node.name if isinstance(node, ast.ExceptHandler) else None


def _import_time_reads(tree: ast.Module) -> set[str]:
    """Return the names code that runs at import reads.

    Function and lambda bodies only run when called and are skipped; their
    decorators, defaults, and annotations run at import and are not.
    """
    reads: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            reads.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            stack += [*node.decorator_list, node.args]
        elif isinstance(node, ast.Lambda):
            stack.append(node.args)
        else:
            stack.extend(ast.iter_child_nodes(node))
    return reads


def _only_imported_from_typing(tree: ast.Module, name: str) -> bool:
    """Return True if *name* is never stored and only imported from ``typing``."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for alias in node.names:
            if (alias.asname or alias.name.partition(".")[0]) != name:
                continue
            from_typing = (
                isinstance(node, ast.ImportFrom)
                and node.module in {"typing", "typing_extensions"}
                and not node.level
                and alias.name == name
            )
            if not from_typing:
                return False
    return name not in _stores(tree)


def _is_session_ref(node: ast.expr, bound: dict[str, _Kind]) -> bool:
    if isinstance(node, ast.Attribute):
        return (
            node.attr == "session"
            and isinstance(node.value, ast.Name)
            and bound.get(node.value.id) is _Kind.MODULE
        )
    return isinstance(node, ast.Name) and bound.get(node.id) is _Kind.SESSION


def _explicit_name(call: ast.Call, default: str | None) -> str | None:
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
    bottom-up. Any name a decorator expression stores is treated as rebound
    for all of them. nox falls back to the wrapped callable's ``__name__``,
    which a lower decorator other than ``@nox.session``/``@nox.parametrize``
    may change, so above one only an explicit ``name=`` is trusted. A
    decorator whose name cannot be read statically contributes nothing rather
    than a name ``nox -s`` would reject.
    """
    seen = _without(bound, _stores(*func.decorator_list))
    names: list[str] = []
    default: str | None = func.name
    for decorator in reversed(func.decorator_list):
        if _is_session_ref(decorator, seen):
            if default is not None:
                names.append(default)
        elif isinstance(decorator, ast.Call) and _is_session_ref(decorator.func, seen):
            name = _explicit_name(decorator, default)
            if name is not None:
                names.append(name)
        elif not _is_parametrize(decorator, seen):
            default = None  # An unknown decorator may rename the function.
    return names


def _is_parametrize(node: ast.expr, bound: dict[str, _Kind]) -> bool:
    """Return True for ``@nox.parametrize(...)``, which keeps ``__name__``."""
    func = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "parametrize"
        and isinstance(func.value, ast.Name)
        and bound.get(func.value.id) is _Kind.MODULE
    )


def _first_line(func: ast.FunctionDef) -> str | None:
    lines = (ast.get_docstring(func) or "").strip().splitlines()
    return lines[0].strip() if lines else None


class _Walker:
    """Follow a noxfile's straight-line module code, tracking nox bindings.

    Blocks may contain imports, plain statements, ``if`` statements, and
    ``try`` statements (not ``except*``) whose parts are themselves such
    blocks. A ``raise`` ends its block, but only under an ``if`` whose test
    uses a name or call (a runtime guard): one in a ``try``, a ``class`` body,
    or under a literal-only test makes the file unpredictable. Sessions
    register only from top-level ``def`` statements. Anything else raises
    ``_Unpredictable``.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.sessions: dict[str, str | None] = {}
        self._type_checking_safe = _only_imported_from_typing(tree, "TYPE_CHECKING")
        # Set once `from typing import TYPE_CHECKING` has run at module level.
        self._type_checking_bound = False
        # `__name__` is never imported from typing, so this means "never bound".
        self._main_name = _only_imported_from_typing(tree, "__name__")
        # A `global`/`nonlocal` anywhere (e.g. in a function called at import)
        # can rebind a module-level name out of statement order.
        self._unstable = {
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Global | ast.Nonlocal)
            for name in node.names
        }

    def block(
        self, body: list[ast.stmt], bound: dict[str, _Kind], *, top: bool
    ) -> dict[str, _Kind] | None:
        """Return the bindings after *body*, or None if it always raises."""
        state: dict[str, _Kind] | None = bound
        for node in body:
            if state is None:
                break  # Unreachable after a raise.
            state = self._statement(node, state, top=top)
            if state is not None:
                state = _without(state, self._unstable)
        return state

    def _statement(
        self, node: ast.stmt, bound: dict[str, _Kind], *, top: bool
    ) -> dict[str, _Kind] | None:
        if isinstance(node, ast.Import | ast.ImportFrom):
            return self._import(node, bound, top=top)
        if isinstance(node, ast.FunctionDef) and top:
            # A later definition under the same name replaces the earlier one,
            # as in nox's own registry, while keeping the first one's position.
            description = _first_line(node)
            for name in _session_names(node, bound):
                self.sessions[name] = description
        if (
            isinstance(node, ast.ClassDef)
            and self.block(node.body, {}, top=False) is None
        ):
            # A class body runs at import, under the same rules as module code;
            # one that always raises means the import always fails.
            return None
        if isinstance(node, _PLAIN):
            return _without(bound, _statement_stores(node))
        if isinstance(node, ast.Raise):
            return None
        if isinstance(node, ast.If):
            return self._if(node, bound)
        if isinstance(node, ast.Try):
            return self._try(node, bound)
        raise _Unpredictable  # Loops, `with`, `match`, `assert`, `except*`, ...

    def _import(
        self, node: ast.Import | ast.ImportFrom, bound: dict[str, _Kind], *, top: bool
    ) -> dict[str, _Kind]:
        if _unknown_star_import(node):
            raise _Unpredictable  # It may rebind any name.
        if top and _imports_type_checking(node):
            self._type_checking_bound = True
        return _apply_import(node, bound)

    def _if(self, node: ast.If, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        type_checking = self._type_checking_safe and self._type_checking_bound
        if _never_runs(node, type_checking=type_checking, main_name=self._main_name):
            return bound
        if _has_literal_condition(node.test) and _raises_within(node):
            # `if True: raise` always raises; nur does not evaluate conditions.
            raise _Unpredictable
        bound = _without(bound, _stores(node.test))
        branches = [self.block(b, bound, top=False) for b in (node.body, node.orelse)]
        return _merge_continuing(branches)

    def _try(self, node: ast.Try, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        if _raises_within(node):
            # Whether a handler catches an explicit raise depends on classes nur
            # does not model, so a `try` with one is unpredictable.
            raise _Unpredictable
        body = self.block(node.body, bound, top=False)
        completed = None if body is None else self.block(node.orelse, body, top=False)
        # A handler may start after any statement of the body ran.
        start = _without(bound, _stores(*node.body))
        paths = [completed]
        for handler in node.handlers:
            names = _stores(handler.type) | ({handler.name} if handler.name else set())
            after = self.block(handler.body, _without(start, names), top=False)
            # Python deletes an `except ... as name` target when it exits.
            paths.append(None if after is None else _without(after, names))
        state = _merge_continuing(paths)
        if state is None:
            return None
        return self.block(node.finalbody, state, top=False)


def _raises_within(node: ast.AST) -> bool:
    """Return True if *node* holds a ``raise`` that runs at import time.

    Function and lambda bodies only run when called, so they are skipped.
    """
    stack: list[ast.AST] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Raise):
            return True
        if current is not node and isinstance(
            current, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
        ):
            continue
        stack.extend(ast.iter_child_nodes(current))
    return False


def _literal_only(expr: ast.expr) -> bool:
    """Return True for an expression built without names, calls, or attributes."""
    return not any(
        isinstance(child, ast.Name | ast.Call | ast.Attribute)
        for child in ast.walk(expr)
    )


def _has_literal_condition(test: ast.expr) -> bool:
    """Return True if a condition's truth may hinge on a literal part.

    ``if flag or True:`` is always true, so a raise under it always runs;
    nur does not evaluate conditions, so a test, or an ``and``/``or``/``not``
    operand, whose truth is fixed (see ``_known_truth``) counts, as does a
    literal-only comparison.
    """
    if any(isinstance(n, ast.Compare) and _literal_only(n) for n in ast.walk(test)):
        return True
    return any(_known_truth(operand) for operand in _truth_operands(test))


def _truth_operands(test: ast.expr) -> list[ast.expr]:
    """Return *test* and the ``and``/``or``/``not`` operands its truth rests on."""
    operands = [test]
    if isinstance(test, ast.BoolOp):
        for value in test.values:
            operands += _truth_operands(value)
    elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        operands += _truth_operands(test.operand)
    return operands


def _known_truth(expr: ast.expr) -> bool:
    """Return True if *expr*'s truth is fixed: a literal, display, or lambda.

    ``(flag,)`` is always true, whatever ``flag`` is.
    """
    displays = ast.Tuple | ast.List | ast.Set | ast.Dict | ast.JoinedStr | ast.Lambda
    return _literal_only(expr) or isinstance(expr, displays)


def _merge_continuing(paths: list[dict[str, _Kind] | None]) -> dict[str, _Kind] | None:
    """Merge the paths that continue; None when every path raises."""
    continuing = [path for path in paths if path is not None]
    return _merge(continuing) if continuing else None


def _scan(tree: ast.Module) -> dict[str, str | None] | None:
    """Return the sessions a straight-line noxfile registers, or None.

    None means nox's registry cannot be predicted without running the file: it
    uses an unsupported construct, or its module code always raises.
    """
    walker = _Walker(tree)
    try:
        if walker.block(tree.body, {}, top=True) is None:
            return None
    except _Unpredictable:
        return None
    return walker.sessions


def parse_noxfile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract nox sessions from *text* by AST parsing, never importing it.

    ``nox --list`` imports and executes ``noxfile.py`` to build its registry, so
    discovery reads ``@nox.session``-decorated functions from the syntax tree
    instead. It only trusts straight-line noxfiles: module code may use
    imports, plain statements (assignment, ``del``, expression, ``pass``,
    ``def``, ``class``), and ``if``/``try`` blocks made of those, where a
    ``raise`` ends its branch. Sessions register only from top-level ``def``
    statements. Anything else (loops, ``with``, ``match``, ``assert``,
    ``except*``, a star import from another module) makes nox's registry
    depend on control flow nur does not follow, so nothing is listed rather
    than a session nox may not register. ``python=[...]`` and
    ``@nox.parametrize`` variants are surfaced under their base name
    (``nox -s <name>`` still runs every variant).
    """
    # The noxfile's own SyntaxWarnings (e.g. invalid escapes) are nox's to
    # report when it runs; nur only reads names, so keep them off every listing.
    with warnings.catch_warnings(action="ignore", category=SyntaxWarning):
        tree = ast.parse(text, filename=source_file)
        # Some trees parse but cannot compile (a repeated keyword argument, a
        # module-level `return`); nox fails to import those, so list nothing.
        # Compiling builds a code object without executing any of it.
        compile(tree, source_file, "exec", dont_inherit=True)
    if _mutates_namespace(tree) or _reads_unbound_name(tree):
        return []
    sessions = _scan(tree)
    if sessions is None:
        return []
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
