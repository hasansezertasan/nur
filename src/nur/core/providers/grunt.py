"""Discover Grunt tasks by parsing Gruntfile.js, without running Grunt."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
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

__all__ = ["GruntProvider", "parse_gruntfile"]

log = logging.getLogger("nur")
_SOURCE_FILE = "Gruntfile.js"
_REGISTER = frozenset({"registerTask", "registerMultiTask"})
# `registerTask(name, description, taskList|fn)`: only the three-argument form
# carries a description in its second argument.
_DESCRIPTION_ARITY = 3
# A registered name must survive the `grunt <name>` command line: whitespace
# would split it into several tasks and a colon would be misread as a
# `task:target` reference, so neither is faithfully runnable.
_NAME = re.compile(r"[^\s:]+")


def _name(node: Node | None) -> str | None:
    value = _plain_string(node)
    return value if value is not None and _NAME.fullmatch(value) else None


def _register_kind(node: Node) -> str | None:
    """Return the register method name for `grunt.register*` calls, else None."""
    if node.type != "call_expression":
        return None
    func = node.child_by_field_name("function")
    if func is None or func.type != "member_expression":
        return None
    prop = func.child_by_field_name("property")
    obj = func.child_by_field_name("object")
    if prop is None or obj is None or prop.type != "property_identifier":
        return None
    kind = _node_text(prop)
    if kind not in _REGISTER:
        return None
    if obj.type == "identifier":
        return kind if _node_text(obj) == "grunt" else None
    return _task_register_kind(obj, kind)


def _task_register_kind(obj: Node, kind: str) -> str | None:
    """Return ``kind`` for `grunt.task.register*` receivers, else None."""
    if obj.type != "member_expression":
        return None
    receiver = obj.child_by_field_name("object")
    task_prop = obj.child_by_field_name("property")
    if receiver is None or task_prop is None:
        return None
    if receiver.type == "identifier" and _node_text(receiver) == "grunt":
        return kind if _node_text(task_prop) == "task" else None
    return None


def _arguments(node: Node) -> list[Node]:
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return []
    return [child for child in arguments.named_children if child.type != "comment"]


def _description(arguments: list[Node]) -> str | None:
    # With any other arity the second argument is the task body/alias, not a
    # description.
    if len(arguments) != _DESCRIPTION_ARITY:
        return None
    value = _plain_string(arguments[1])
    if value is None or not value.strip():
        return None
    return value.strip().splitlines()[0]


def _declarations(root: Node, source_file: str) -> Iterator[Task]:
    for node in _walk(root):
        if _register_kind(node) is None:
            continue
        arguments = _arguments(node)
        if not arguments:
            continue
        name = _name(arguments[0])
        if name is None:
            continue
        yield Task(
            name=name,
            prefix="grunt",
            argv_base=("grunt", name),
            description=_description(arguments),
            source_file=source_file,
        )


def parse_gruntfile(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Extract literal Grunt task candidates without evaluating JavaScript.

    Direct ``grunt.registerTask`` / ``grunt.registerMultiTask`` calls (and
    their ``grunt.task.*`` aliases) with a literal name are supported, at any
    nesting level — conditional guards are not evaluated, so guarded
    declarations are still listed. Task bodies and alias lists stay opaque;
    plugin-loaded tasks (``loadNpmTasks``), renamed tasks, external task
    files, computed/dynamic names, and ``task:target`` spellings are skipped.
    Parser errors skip the file. Results are syntactic task candidates, not a
    promise that Grunt will register or successfully execute each task.
    """
    root = _parse(text, source_file)
    if root is None:
        return []
    tasks: dict[str, Task] = {}
    for declared in _declarations(root, source_file):
        merged = declared
        existing = tasks.get(declared.name)
        if existing is not None and declared.description is None:
            merged = replace(declared, description=existing.description)
        tasks[declared.name] = merged
    return list(tasks.values())


class GruntProvider:
    prefix = "grunt"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, error)
            return []
        return parse_gruntfile(text)
