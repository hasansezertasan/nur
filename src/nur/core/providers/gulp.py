"""Discover Gulp tasks by parsing gulpfile.js, without running Gulp."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._javascript import (
    node_text as _node_text,
    parse as _parse,
    plain_string as _plain_string,
    walk as _walk,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tree_sitter import Node

__all__ = ["GulpProvider", "parse_gulpfile"]

log = logging.getLogger("nur")
# Gulp loads `gulpfile.js` (or capitalized, like Makefile) by default; ESM and
# explicit-CommonJS projects use the sibling variants. Only the first file
# present is parsed (single-file discovery, like the mise provider).
_SOURCE_FILES = ("gulpfile.js", "Gulpfile.js", "gulpfile.mjs", "gulpfile.cjs")
# A registered name must survive the `gulp <name>` command line: whitespace
# would split it into several tasks, so it is not faithfully runnable.
_NAME = re.compile(r"\S+")


def _name(node: Node | None) -> str | None:
    value = _plain_string(node)
    if value is not None and _NAME.fullmatch(value):
        return value
    if node is not None and node.type == "property_identifier":
        value = _node_text(node)
        return value if _NAME.fullmatch(value) else None
    return None


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _task_receiver(func: Node) -> bool:
    """Whether a call target is `gulp.task` or a destructured bare `task`."""
    if func.type == "member_expression":
        obj = func.child_by_field_name("object")
        prop = func.child_by_field_name("property")
        return (
            obj is not None
            and prop is not None
            and obj.type == "identifier"
            and prop.type == "property_identifier"
            and _node_text(obj) == "gulp"
            and _node_text(prop) == "task"
        )
    # Destructured `const { task } = require('gulp')`, per gulp's own docs.
    return func.type == "identifier" and _node_text(func) == "task"


def _call_task_name(node: Node) -> str | None:
    """Return the literal name of a ``gulp.task(...)``/``task(...)`` call."""
    if node.type != "call_expression":
        return None
    func = node.child_by_field_name("function")
    if func is None or not _task_receiver(func):
        return None
    arguments = _arguments(node)
    if not arguments:
        return None
    # A bare function reference (`task(build)`) registers under the function's
    # own name, which needs scope resolution; only literal names are read.
    return _name(arguments[0]) if arguments[0].type == "string" else None


def _module_exports(node: Node) -> bool:
    """Whether ``node`` is the `module.exports` member expression."""
    if node.type != "member_expression":
        return False
    obj = node.child_by_field_name("object")
    prop = node.child_by_field_name("property")
    return (
        obj is not None
        and prop is not None
        and obj.type == "identifier"
        and _node_text(obj) == "module"
        and prop.type == "property_identifier"
        and _node_text(prop) == "exports"
    )


def _exports_object(node: Node | None) -> bool:
    """Whether ``node`` is `exports` or `module.exports`."""
    if node is None:
        return False
    if node.type == "identifier":
        return _node_text(node) == "exports"
    return _module_exports(node)


def _exports_key(node: Node) -> tuple[Node | None, Node | None]:
    """Return the (object, key) of an exports assignment target.

    Handles `exports.name` / `module.exports.name` (member access) and
    `exports['name']` (string index); anything else yields `(None, None)`.
    """
    if node.type != "assignment_expression":
        return None, None
    left = node.child_by_field_name("left")
    if left is None:
        return None, None
    if left.type == "member_expression":
        return left.child_by_field_name("object"), left.child_by_field_name("property")
    if left.type == "subscript_expression":
        return left.child_by_field_name("object"), left.child_by_field_name("index")
    return None, None


def _exports_name(node: Node) -> str | None:
    """Return the task name of an `exports.name = ...` assignment."""
    obj, key = _exports_key(node)
    if not _exports_object(obj):
        return None
    return _name(key)


def _description_assignment(node: Node) -> tuple[str, str] | None:
    """Return ``(task, description)`` for `<task>.description = '...'`."""
    if node.type != "assignment_expression":
        return None
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or left.type != "member_expression" or right is None:
        return None
    obj = left.child_by_field_name("object")
    prop = left.child_by_field_name("property")
    if (
        obj is None
        or prop is None
        or obj.type != "identifier"
        or prop.type != "property_identifier"
        or _node_text(prop) != "description"
    ):
        return None
    description = _plain_string(right)
    if description is None or not description.strip():
        return None
    return _node_text(obj), description.strip().splitlines()[0]


def _export_names(node: Node) -> Iterator[str]:
    """Yield task names from an `export ...` statement.

    Yields:
        The exported task name for plain function/const exports, `default`
        for default exports, and each `export {a as b}` alias (or name).
    """
    if node.type != "export_statement":
        return
    declaration = node.child_by_field_name("declaration")
    if declaration is not None:
        if declaration.type == "function_declaration":
            name = declaration.child_by_field_name("name")
            if name is not None:
                yield _node_text(name)
        elif declaration.type == "lexical_declaration":
            yield from _lexical_names(declaration)
        return
    if node.child_by_field_name("value") is not None:
        # `export default <expr>` runs as `gulp default`.
        yield "default"
        return
    yield from _clause_names(node)


def _lexical_names(declaration: Node) -> Iterator[str]:
    """Yield task names from an exported lexical declaration.

    Yields:
        The identifier of each plain declarator; destructured declarators
        have no single runnable name and are skipped.
    """
    for child in declaration.named_children:
        if child.type != "variable_declarator":
            continue
        name = child.child_by_field_name("name")
        if name is not None and name.type == "identifier":
            yield _node_text(name)


def _clause_names(node: Node) -> Iterator[str]:
    """Yield task names from an `export {...}` clause.

    Yields:
        Each specifier's alias (or name); string aliases that cannot survive
        the `gulp <name>` command line are skipped.
    """
    for child in node.named_children:
        if child.type != "export_clause":
            continue
        for spec in child.named_children:
            if spec.type != "export_specifier":
                continue
            alias = spec.child_by_field_name("alias")
            name = spec.child_by_field_name("name")
            label = alias if alias is not None else name
            if label is not None and _NAME.fullmatch(_node_text(label)):
                yield _node_text(label)


def parse_gulpfile(text: str, source_file: str = _SOURCE_FILES[0]) -> list[Task]:
    """Extract literal Gulp task candidates without evaluating JavaScript.

    Supported: literal ``gulp.task('name', ...)`` / bare ``task('name', ...)``
    calls, CommonJS ``exports.name`` / ``module.exports.name`` /
    ``exports['name']`` assignments, and ESM ``export function`` /
    ``export const`` / ``export default`` / ``export {a as b}`` forms.
    ``<task>.description`` string assignments supply optional metadata.
    Function references (``task(build)``), ``series()``/``parallel()``
    composition, computed keys, object-literal ``module.exports = {...}``,
    re-exports, and task bodies stay opaque. Conditional guards are not
    evaluated. Parser errors skip the file. Results are syntactic task
    candidates, not a promise that Gulp will register or run each task.
    """
    root = _parse(text, source_file)
    if root is None:
        return []
    names: list[str] = []
    descriptions: dict[str, str] = {}
    for node in _walk(root):
        # Every matcher rejects foreign node types itself, so each runs
        # unconditionally; that also pins the "never crashes" contract.
        described = _description_assignment(node)
        if described is not None:
            descriptions[described[0]] = described[1]
        found = _call_task_name(node)
        if found is None:
            found = _exports_name(node)
        if found is None:
            for exported in _export_names(node):
                if exported not in names:
                    names.append(exported)
        elif found not in names:
            names.append(found)
    return [
        Task(
            name=name,
            prefix="gulp",
            argv_base=("gulp", name),
            description=descriptions.get(name),
            source_file=source_file,
        )
        for name in names
    ]


def _config_file(cwd: Path) -> str | None:
    for filename in _SOURCE_FILES:
        if (cwd / filename).is_file():
            return filename
    return None


class GulpProvider:
    prefix = "gulp"

    def detect(self, cwd: Path) -> bool:
        return _config_file(cwd) is not None

    def discover(self, cwd: Path) -> list[Task]:
        filename = _config_file(cwd)
        if filename is None:
            return []
        try:
            text = (cwd / filename).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            log.warning("nur: skipping %s (%s)", filename, error)
            return []
        return parse_gulpfile(text, source_file=filename)
