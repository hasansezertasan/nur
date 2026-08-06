from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["CargoMakeProvider"]


log = logging.getLogger("nur")


def _load_tasks(cwd: Path) -> dict[str, object] | None:
    path = cwd / "Makefile.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping Makefile.toml (%s)", exc)
        return None
    tasks = data.get("tasks")
    return tasks if isinstance(tasks, dict) else None


def _definition(value: dict[str, Any]) -> str:
    # cargo-make tasks run either a `command` (+ `args`) or a `script`; a task
    # may also be composite (only `dependencies`), in which case there is no
    # local command to show.
    command = value.get("command")
    if isinstance(command, str):
        args = value.get("args")
        if isinstance(args, list):
            return " ".join([command, *(str(a) for a in args)])
        return command
    script = value.get("script")
    if isinstance(script, list):
        # `script` is a list of sequential shell lines; join like the mise/npm
        # providers do for multi-command tasks.
        return " && ".join(str(line) for line in script)
    if isinstance(script, str):
        return script
    return ""


class CargoMakeProvider:
    prefix = "cargo-make"

    def detect(self, cwd: Path) -> bool:
        return bool(_load_tasks(cwd))

    def discover(self, cwd: Path) -> list[Task]:
        tasks_table = _load_tasks(cwd)
        if not tasks_table:
            return []
        tasks: list[Task] = []
        for name, value in tasks_table.items():
            if not isinstance(value, dict):
                continue
            # cargo-make hides `private = true` tasks from its task listing.
            if value.get("private") is True:
                continue
            description = value.get("description")
            tasks.append(
                Task(
                    name=name,
                    prefix=self.prefix,
                    argv_base=("cargo", "make", name),
                    description=description if isinstance(description, str) else None,
                    definition=_definition(value),
                    source_file="Makefile.toml",
                )
            )
        return tasks
