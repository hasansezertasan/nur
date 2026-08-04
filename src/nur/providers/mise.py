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
        data = tomllib.loads((cwd / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", name, exc)
        return None
    tasks = data.get("tasks")
    return (name, tasks) if isinstance(tasks, dict) else None


def _join_run(body: object) -> str:
    # mise `run` may be a list of sequential shell commands.
    if isinstance(body, list):
        return " && ".join(str(x) for x in body)
    return str(body)


def _definition_and_help(value: object) -> tuple[str, str | None]:
    if isinstance(value, dict):
        body: Any = value.get("run", "")
        desc = value.get("description")
        return _join_run(body), (desc if isinstance(desc, str) else None)
    # Shorthand form: `name = "cmd"` or `name = ["cmd1", "cmd2"]`.
    return _join_run(value), None


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
            if isinstance(value, dict) and value.get("hide") is True:
                continue  # mise hides `hide = true` tasks from `mise tasks ls`
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
