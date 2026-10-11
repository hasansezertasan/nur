"""Discover Turborepo tasks by parsing turbo.json, without running turbo."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._jsonc import loads_jsonc

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["TurboProvider", "parse_turbo"]

log = logging.getLogger("nur")
_SOURCE_FILE = "turbo.json"
_ROOT_PREFIX = "//#"


def _task_names(data: object) -> dict[str, object] | None:
    # Turborepo v2 registers runnable tasks under `tasks`; v1 used `pipeline`.
    # Prefer `tasks` when both exist; each key is runnable via `turbo run`.
    if not isinstance(data, dict):
        return None
    tasks = data.get("tasks", data.get("pipeline"))
    return tasks if isinstance(tasks, dict) else None


def parse_turbo(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Parse a workspace root's ``turbo.json`` *text* without executing anything.

    Deliberately does NOT run ``turbo run`` (or ``--dry-run``) to enumerate
    tasks: that boots Turborepo's daemon and resolves the package graph, which
    can run repository-controlled code during discovery. Reading the task keys
    directly is safe, at the cost of not verifying that a member ``package.json``
    actually implements each script. ``definition`` stays empty because
    ``turbo.json`` holds orchestration config (``dependsOn``, ``outputs``),
    not the command itself.
    """
    tasks_table = _task_names(loads_jsonc(text, source_file))
    if not tasks_table:
        return []
    tasks: list[Task] = []
    for key, value in tasks_table.items():
        if not isinstance(value, dict):
            continue
        # Root tasks (`//#lint:root`) run root scripts via `turbo run <name>`.
        name = str(key).removeprefix(_ROOT_PREFIX)
        if not name:
            continue
        tasks.append(
            Task(
                name=name,
                prefix="turbo",
                argv_base=("turbo", "run", name),
                source_file=source_file,
            )
        )
    return tasks


class TurboProvider:
    prefix = "turbo"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        return parse_turbo(text)
