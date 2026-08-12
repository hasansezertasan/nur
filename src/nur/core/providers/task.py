from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["TaskfileProvider", "parse_taskfile"]


log = logging.getLogger("nur")


def parse_taskfile(text: str, source_file: str = "Taskfile.yml") -> list[Task]:
    """Parse a Taskfile's YAML *text* without executing anything.

    Deliberately does NOT run ``task --list``: go-task evaluates dynamic ``sh:``
    variables while loading/compiling the file, so listing could run
    repository-controlled commands during discovery. Reading the YAML directly
    is safe, at the cost of not resolving ``includes:`` or generated tasks.
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return []
    tasks_table = data.get("tasks")
    if not isinstance(tasks_table, dict):
        return []
    tasks: list[Task] = []
    for name, body in tasks_table.items():
        description: str | None = None
        if isinstance(body, dict):
            if body.get("internal") is True:  # go-task hides internal tasks from --list
                continue
            desc = body.get("desc")
            description = desc if isinstance(desc, str) else None
        tasks.append(
            Task(
                name=str(name),
                prefix="task",
                argv_base=("task", str(name)),
                description=description,
                source_file=source_file,
            )
        )
    return tasks


class TaskfileProvider:
    prefix = "task"

    def _source_file(self, cwd: Path) -> str:
        return "Taskfile.yml" if (cwd / "Taskfile.yml").is_file() else "Taskfile.yaml"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "Taskfile.yml").is_file() or (cwd / "Taskfile.yaml").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        source_file = self._source_file(cwd)
        try:
            text = (cwd / source_file).read_text()
        except OSError as exc:
            log.warning("nur: skipping %s (%s)", source_file, exc)
            return []
        try:
            return parse_taskfile(text, source_file)
        except yaml.YAMLError as exc:
            log.warning("nur: skipping %s (%s)", source_file, exc)
            return []
