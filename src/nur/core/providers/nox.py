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
# `nox.__all__`, which `from nox import *` binds.
_NOX_STAR = {
    "Session": "OTHER",
    "main": "OTHER",
    "needs_version": "OTHER",
    "options": "OTHER",
    "param": "OTHER",
    "parametrize": "PARAMETRIZE",
    "project": "OTHER",
    "session": "SESSION",
}
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
    "__setattr__",
    "__delattr__",
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
    """What a module-level name is certainly bound to.

    Bindings map names to a kind. A name missing from the mapping may be
    unbound, so reading it at import time may raise ``NameError``.
    """

    MODULE = enum.auto()  # a nox module: `<name>.session` is the decorator
    SESSION = enum.auto()  # the `session` decorator itself
    UV_MODULE = enum.auto()  # the nox-uv module: `<name>.session` wraps nox's
    UV_SESSION = enum.auto()  # nox-uv's drop-in `session` decorator
    PARAMETRIZE = enum.auto()  # `nox.parametrize`, which keeps `__name__`
    OTHER = enum.auto()  # bound, but not to anything nox


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
    return any(_mutates(node) for node in ast.walk(tree))


def _mutates(node: ast.AST) -> bool:
    if isinstance(node, ast.Attribute):
        stored = isinstance(node.ctx, ast.Store | ast.Del)
        return (node.attr == "session" and stored) or node.attr == "__dict__"
    if isinstance(node, ast.Call):
        func = node.func
        name = func.id if isinstance(func, ast.Name) else None
        if isinstance(func, ast.Attribute):
            name = func.attr  # e.g. `builtins.setattr(...)`
        return name in _MUTATORS or _computed_getattr(node)
    if isinstance(node, ast.Constant):
        return node.value == "__dict__"  # e.g. `getattr(nox, "__dict__")`.
    if isinstance(node, ast.ImportFrom) and node.module == "builtins":
        # `from builtins import setattr as s` hides the call.
        return any(alias.name in _MUTATORS | {"*"} for alias in node.names)
    return False


def _computed_getattr(node: ast.Call) -> bool:
    """Return True for ``getattr(obj, name)`` with a non-literal attribute name."""
    if len(node.args) < 2:  # noqa: PLR2004  # (object, name)
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    attr = node.args[1]
    return name in {"getattr", "__getattribute__"} and not (
        isinstance(attr, ast.Constant) and isinstance(attr.value, str)
    )


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


def _bind(node: ast.stmt, bound: dict[str, _Kind]) -> dict[str, _Kind]:
    """Apply a plain statement's bindings to *bound*.

    Every name it may store or delete loses its nox kind (over-approximated,
    counting comprehension and lambda internals); only names it certainly binds
    in module scope become defined. A ``def`` or ``class`` binds its name and
    evaluates its header here, but its body is its own scope.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        header = _stores(*node.decorator_list, node.args, node.returns)
        return _without(bound, header) | {node.name: _Kind.OTHER}
    if isinstance(node, ast.ClassDef):
        header = _stores(*node.decorator_list, *node.bases, *node.keywords)
        return _without(bound, header) | {node.name: _Kind.OTHER}
    result = _without(bound, _stores(node))
    for name in _certain_targets(node):
        result[name] = _Kind.OTHER
    return result


def _certain_targets(node: ast.stmt) -> set[str]:
    """Return the module names an assignment statement certainly binds."""
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign | ast.AugAssign):
        targets = [node.target] if getattr(node, "value", None) is not None else []
    else:
        return set()
    names: set[str] = set()
    for target in targets:
        stack: list[ast.expr] = [target]
        while stack:
            current = stack.pop()
            if isinstance(current, ast.Name):
                names.add(current.id)
            elif isinstance(current, ast.Tuple | ast.List):
                stack.extend(current.elts)
            elif isinstance(current, ast.Starred):
                stack.append(current.value)
    return names


def _reads(*nodes: ast.AST | None) -> set[str]:
    """Return the names evaluating *nodes* reads in the enclosing scope.

    Names bound inside a comprehension are its own; a lambda's body runs only
    when called, though its defaults are evaluated here.
    """
    reads: set[str] = set()
    for node in nodes:
        if node is not None:
            _collect_reads(node, reads, local=frozenset())
    return reads


def _collect_reads(node: ast.AST, reads: set[str], *, local: frozenset[str]) -> None:
    if isinstance(node, ast.Lambda):
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                _collect_reads(default, reads, local=local)
        return
    if isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
        # Each generator's iterable is read before its own target is bound (the
        # first one in the enclosing scope); its filters and everything after
        # see that target, and the element sees all of them.
        # A walrus inside binds the enclosing scope, so it is not local here.
        inner = local
        for generator in node.generators:
            _collect_reads(generator.iter, reads, local=inner)
            inner |= _stores(generator.target)
            for condition in generator.ifs:
                _collect_reads(condition, reads, local=inner)
        parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        for part in parts:
            _collect_reads(part, reads, local=inner)
        return
    if (
        isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in local
    ):
        reads.add(node.id)
    for child in ast.iter_child_nodes(node):
        _collect_reads(child, reads, local=local)


def _deleted_names(node: ast.stmt) -> set[str]:
    """Return the plain names a ``del`` statement deletes."""
    if not isinstance(node, ast.Delete):
        return set()
    return {
        child.id
        for target in node.targets
        for child in ast.walk(target)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Del)
    }


def _annotated_reads(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.AnnAssign,
    *,
    lazy_annotations: bool,
) -> set[str]:
    """Return the reads of a statement with annotations, skipping lazy ones."""
    if isinstance(node, ast.AnnAssign):
        if lazy_annotations:
            return _reads(node.target, node.value) - _certain_targets(node)
        return _reads(node)
    if lazy_annotations:
        defaults = [*node.args.defaults, *node.args.kw_defaults]
        return _reads(*node.decorator_list, *defaults)
    return _reads(*node.decorator_list, node.args, node.returns)


def _statement_reads(node: ast.stmt, *, lazy_annotations: bool) -> set[str]:
    """Return the names a statement reads when it runs (its header only).

    With ``from __future__ import annotations`` annotations are never
    evaluated, so they are not reads; otherwise they are (eagerly, before
    Python 3.14), which nur assumes since nox may run an older Python.
    """
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.AnnAssign):
        return _annotated_reads(node, lazy_annotations=lazy_annotations)
    if isinstance(node, ast.ClassDef):
        return _reads(*node.decorator_list, *node.bases, *node.keywords)
    if isinstance(node, ast.If):
        return _reads(node.test)
    if isinstance(node, ast.Try | ast.Import | ast.ImportFrom):
        return set()
    reads = _reads(node)
    if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
        reads.add(node.target.id)  # `x += 1` reads `x` first.
    return reads


def _without(bound: dict[str, _Kind], names: set[str]) -> dict[str, _Kind]:
    return {name: kind for name, kind in bound.items() if name not in names}


def _merge(paths: list[dict[str, _Kind]]) -> dict[str, _Kind]:
    """Keep only the bindings every path agrees on.

    nox's and nox-uv's ``session`` decorators both register sessions, so a
    name bound to either on every path stays a session decorator, limited to
    the keywords nox accepts (``from nox_uv import session`` falling back to
    ``from nox import session``). Otherwise every kind must match.
    """
    first, *rest = paths
    merged: dict[str, _Kind] = {}
    for name, kind in first.items():
        kinds = {kind, *(path.get(name) for path in rest)}
        if len(kinds) == 1:
            merged[name] = kind
        elif kinds <= {_Kind.SESSION, _Kind.UV_SESSION}:
            merged[name] = _Kind.SESSION
    return merged


def _unknown_star_import(node: ast.stmt) -> bool:
    """Return True for ``from x import *`` of a module other than nox itself.

    nox-uv defines no ``__all__``, so what its star import binds varies.
    """
    return (
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        and not (node.module == "nox" and not node.level)
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
            result[name] = _MODULE_KINDS.get(target, _Kind.OTHER)
        return result
    from_nox = node.module in _NOX_MODULES and not node.level
    kinds = {"session": _Kind.SESSION}
    if node.module == "nox":
        kinds["parametrize"] = _Kind.PARAMETRIZE
    else:
        kinds["session"] = _Kind.UV_SESSION
    for alias in node.names:
        if alias.name == "*":  # Only `from nox import *` gets here.
            result.update({n: _Kind[kind] for n, kind in _NOX_STAR.items()})
            continue
        name = alias.asname or alias.name
        known = from_nox and alias.name in kinds
        result[name] = kinds[alias.name] if known else _Kind.OTHER
    return result


_MODULE_KINDS = {"nox": _Kind.MODULE, "nox_uv": _Kind.UV_MODULE}
_SESSION_OF = {_Kind.MODULE: _Kind.SESSION, _Kind.UV_MODULE: _Kind.UV_SESSION}
# Keyword arguments `nox.session` accepts; any other raises TypeError.
_NOX_SESSION_KWARGS = frozenset({
    "python",
    "py",
    "reuse_venv",
    "name",
    "venv_backend",
    "venv_params",
    "tags",
    "default",
    "requires",
    "download_python",
    "allow_parallel",
})
# nox-uv adds its own and forwards the rest to `nox.session`.
_UV_SESSION_KWARGS = _NOX_SESSION_KWARGS | {
    "uv_groups",
    "uv_extras",
    "uv_only_groups",
    "uv_all_extras",
    "uv_no_extras",
    "uv_all_groups",
    "uv_no_groups",
    "uv_no_install_project",
    "uv_sync_locked",
    "uv_quiet",
    "uv_packages",
    "uv_all_packages",
}


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


def _session_kwargs(node: ast.expr, bound: dict[str, _Kind]) -> frozenset[str] | None:
    """Return the keywords a session decorator accepts, or None if it isn't one.

    ``nox.session``/``session`` from nox and their nox-uv equivalents count.
    """
    kind: _Kind | None = None
    if isinstance(node, ast.Attribute) and node.attr == "session":
        if isinstance(node.value, ast.Name):
            module = bound.get(node.value.id)
            kind = _SESSION_OF.get(module) if module is not None else None
    elif isinstance(node, ast.Name):
        kind = bound.get(node.id)
    if kind is _Kind.SESSION:
        return _NOX_SESSION_KWARGS
    return _UV_SESSION_KWARGS if kind is _Kind.UV_SESSION else None


def _explicit_name(
    call: ast.Call, default: str | None, accepted: frozenset[str]
) -> str | None:
    """Return the name a ``@nox.session(...)`` call registers, if it is static.

    nox registers ``name or func.__name__``. A ``name=`` that is not a literal,
    a ``**mapping`` that may carry one, or positional arguments make the real
    name unknowable without evaluation, and a keyword not in *accepted* makes
    the call raise ``TypeError``, so nothing registers.
    """
    if call.args or any(keyword.arg not in accepted for keyword in call.keywords):
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
    if any(_decorator_fails(decorator, seen) for decorator in func.decorator_list):
        return []  # Python evaluates every decorator before applying any.
    names: list[str] = []
    default: str | None = func.name
    for decorator in reversed(func.decorator_list):
        call = decorator if isinstance(decorator, ast.Call) else None
        accepted = _session_kwargs(call.func if call else decorator, seen)
        if accepted is not None and call is None:
            if default is not None:
                names.append(default)
        elif accepted is not None and call is not None:
            name = _explicit_name(call, default, accepted)
            if name is not None:
                names.append(name)
        elif not _is_parametrize(decorator, seen):
            default = None  # An unknown decorator may rename the function.
    return names


def _decorator_fails(decorator: ast.expr, bound: dict[str, _Kind]) -> bool:
    """Return True if a nox decorator expression certainly (or may) raise.

    A session call with positional arguments or an unaccepted keyword raises,
    and one with ``**mapping`` may; so does ``parametrize`` used bare or
    without its required arguments.
    """
    call = decorator if isinstance(decorator, ast.Call) else None
    target = call.func if call is not None else decorator
    accepted = _session_kwargs(target, bound)
    if accepted is not None:
        return call is not None and (
            bool(call.args) or any(kw.arg not in accepted for kw in call.keywords)
        )
    if _is_parametrize_ref(target, bound):
        return call is None or not _valid_parametrize_call(call)
    return False


def _is_parametrize(node: ast.expr, bound: dict[str, _Kind]) -> bool:
    """Return True for ``@nox.parametrize(...)``, which keeps ``__name__``.

    It must be called with its two required arguments (``arg_names`` and
    ``arg_values_list``); ``@nox.parametrize()`` raises ``TypeError``.
    """
    if not isinstance(node, ast.Call) or not _valid_parametrize_call(node):
        return False
    return _is_parametrize_ref(node.func, bound)


def _is_parametrize_ref(func: ast.expr, bound: dict[str, _Kind]) -> bool:
    """Return True if *func* names nox's ``parametrize``."""
    if isinstance(func, ast.Name):
        return bound.get(func.id) is _Kind.PARAMETRIZE
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "parametrize"
        and isinstance(func.value, ast.Name)
        and bound.get(func.value.id) is _Kind.MODULE
    )


def _valid_parametrize_call(call: ast.Call) -> bool:
    keywords = {keyword.arg for keyword in call.keywords if keyword.arg is not None}
    if len(keywords) < len(call.keywords) or any(
        isinstance(arg, ast.Starred) for arg in call.args
    ):
        return False  # `*args`/`**kwargs` hide what is passed.
    accepted = ["arg_names", "arg_values_list", "ids"]
    positional = set(accepted[: len(call.args)])
    return (
        len(call.args) <= len(accepted)
        and not positional & keywords
        and keywords <= set(accepted)
        and {"arg_names", "arg_values_list"} <= positional | keywords
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
        self._lazy_annotations = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "__future__"
            and any(alias.name == "annotations" for alias in node.names)
            for node in tree.body
        )
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
            reads = _statement_reads(node, lazy_annotations=self._lazy_annotations)
            if not reads <= state.keys() | _IMPLICIT:
                return None  # Reading a name not yet bound raises NameError.
            if not _deleted_names(node) <= state.keys():
                return None  # `del` of an unbound name (even a builtin) fails.
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
        if isinstance(node, ast.ClassDef) and _deletes_within(node):
            # A class-scope `del` only sees the class namespace, which the
            # module-level bindings nur passes in do not separate out.
            raise _Unpredictable
        if (
            isinstance(node, ast.ClassDef)
            and self.block(node.body, bound, top=False) is None
        ):
            # A class body runs at import, under the same rules as module code;
            # one that always raises means the import always fails.
            return None
        if isinstance(node, _PLAIN):
            return _bind(node, bound)
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
        del top  # Imports bind the same way at any depth.
        if _unknown_star_import(node):
            raise _Unpredictable  # It may rebind any name.
        return _apply_import(node, bound)

    def _if(self, node: ast.If, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        type_checking = self._type_checking_safe and "TYPE_CHECKING" in bound
        if _never_runs(node, type_checking=type_checking, main_name=self._main_name):
            return bound
        bound = _without(bound, _stores(node.test))
        branches = [self.block(b, bound, top=False) for b in (node.body, node.orelse)]
        if None in branches and _has_literal_condition(node.test):
            # `if True: missing` always fails; nur does not evaluate conditions,
            # so it cannot tell whether the failing branch is the one taken.
            raise _Unpredictable
        return _merge_continuing(branches)

    def _try(self, node: ast.Try, bound: dict[str, _Kind]) -> dict[str, _Kind] | None:
        if _raises_within(node):
            # Whether a handler catches an explicit raise depends on classes nur
            # does not model, so a `try` with one is unpredictable.
            raise _Unpredictable
        body = self.block(node.body, bound, top=False)
        if body is None:
            # The body certainly fails (say, a NameError); whether a handler
            # catches that depends on its class, which nur does not model.
            raise _Unpredictable
        completed = self.block(node.orelse, body, top=False)
        # A handler may start after any statement of the body ran, unless the
        # body cannot raise at all (only `pass` and literal expressions).
        start = _without(bound, _stores(*node.body))
        paths = [completed]
        handlers = [] if _cannot_raise(node.body) else node.handlers
        for handler in handlers:
            names = _stores(handler.type) | ({handler.name} if handler.name else set())
            entry = _without(start, names)
            if handler.name:
                entry[handler.name] = _Kind.OTHER
            after = self.block(handler.body, entry, top=False)
            # Python deletes an `except ... as name` target when it exits.
            paths.append(None if after is None else _without(after, names))
        state = _merge_continuing(paths)
        if state is None:
            return None
        return self.block(node.finalbody, state, top=False)


def _deletes_within(node: ast.ClassDef) -> bool:
    """Return True if a class body runs a ``del`` (outside nested functions)."""
    stack: list[ast.AST] = list(node.body)
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Delete):
            return True
        if not isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            stack.extend(ast.iter_child_nodes(current))
    return False


def _cannot_raise(body: list[ast.stmt]) -> bool:
    """Return True for a block of only ``pass`` and literal expressions."""
    return all(
        isinstance(stmt, ast.Pass)
        or (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        for stmt in body
    )


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
    if _mutates_namespace(tree):
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
