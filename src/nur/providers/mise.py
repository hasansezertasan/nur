from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MiseProvider"]


log = logging.getLogger("nur")

# Config files mise reads, in the priority order used here for discovery. Only
# the first existing file is parsed: nur does not replicate mise's full
# multi-file merge, mirroring the Taskfile provider's single-file approach.
CONFIG_FILES = (
    "mise.local.toml",
    "mise.toml",
    ".mise.local.toml",
    ".mise.toml",
    ".config/mise.toml",
)


def _config_file(cwd: Path) -> str | None:
    for name in CONFIG_FILES:
        if (cwd / name).is_file():
            return name
    return None


def _load_tasks(cwd: Path) -> tuple[str, dict[str, object]] | None:
    """Return ``(source_file, tasks_table)``, or None if absent/unreadable."""
    name = _config_file(cwd)
    if name is None:
        return None
    try:
        data = tomllib.loads((cwd / name).read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", name, exc)
        return None
    tasks = data.get("tasks")
    return (name, tasks) if isinstance(tasks, dict) else None


def _definition_and_help(value: object) -> tuple[str, str | None]:
    if isinstance(value, dict):
        body: Any = value.get("run", "")
        # mise `run` may be a list of sequential shell commands.
        if isinstance(body, list):
            body = " && ".join(str(x) for x in body)
        desc = value.get("description")
        return str(body), (desc if isinstance(desc, str) else None)
    return str(value), None


class MiseProvider:
    prefix = "mise"

    def detect(self, cwd: Path) -> bool:
        loaded = _load_tasks(cwd)
        return bool(loaded and loaded[1])

    def discover(self, cwd: Path) -> list[Task]:
        loaded = _load_tasks(cwd)
        if loaded is None:
            return []
        source_file, tasks_table = loaded
        tasks: list[Task] = []
        for name, value in tasks_table.items():
            definition, help_text = _definition_and_help(value)
            tasks.append(
                Task(
                    name=name,
                    prefix=self.prefix,
                    argv_base=("mise", "run", name),
                    description=help_text,
                    definition=definition,
                    source_file=source_file,
                )
            )
        return tasks
