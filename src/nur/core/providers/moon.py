from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import yaml

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MoonProvider", "parse_moon"]


log = logging.getLogger("nur")


def _flatten(value: object) -> str:
    # moon's `command`/`args` accept a string or a list of string tokens; both
    # collapse to a single-line preview. `script` is always a plain string.
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return ""


def _definition(body: dict[str, Any]) -> str:
    # A moon task runs either a `command` (+ optional `args`) or a `script`.
    command = _flatten(body.get("command"))
    if command:
        args = _flatten(body.get("args"))
        return f"{command} {args}" if args else command
    script = body.get("script")
    return script if isinstance(script, str) else ""


def parse_moon(text: str, source_file: str = "moon.yml") -> list[Task]:
    """Parse a moon project's ``moon.yml`` *text* without executing anything.

    Deliberately does NOT run ``moon query tasks``: enumerating tasks that way
    boots moon's toolchain and resolves inherited config, which can run
    repository-controlled commands during discovery. Reading the YAML directly
    is safe, at the cost of not resolving inherited tasks from ``.moon/`` or
    project-qualified ``<project>:<task>`` addressing across the monorepo.
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return []
    tasks_table = data.get("tasks")
    if not isinstance(tasks_table, dict):
        return []
    tasks: list[Task] = []
    for name, body in tasks_table.items():
        if not isinstance(body, dict):
            continue
        options = body.get("options")
        # moon's `internal` tasks cannot be run from the command line (they only
        # exist to be depended on), so they are hidden from listings.
        if isinstance(options, dict) and options.get("internal") is True:
            continue
        description = body.get("description")
        tasks.append(
            Task(
                name=str(name),
                prefix="moon",
                argv_base=("moon", "run", str(name)),
                description=description if isinstance(description, str) else None,
                definition=_definition(body),
                source_file=source_file,
                # moon consumes args after `moon run <task>` as its own options
                # / additional targets; forward passthrough args after a `--`.
                passthrough_prefix=("--",),
            )
        )
    return tasks


class MoonProvider:
    prefix = "moon"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "moon.yml").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / "moon.yml").read_text()
        except OSError as exc:
            log.warning("nur: skipping moon.yml (%s)", exc)
            return []
        try:
            return parse_moon(text)
        except yaml.YAMLError as exc:
            log.warning("nur: skipping moon.yml (%s)", exc)
            return []
