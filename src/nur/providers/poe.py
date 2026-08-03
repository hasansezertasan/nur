from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PoeProvider"]


log = logging.getLogger("nur")


def _load_tasks(cwd: Path) -> dict[str, object] | None:
    path = cwd / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping pyproject.toml (%s)", exc)
        return None
    tool = data.get("tool", {})
    poe: dict[str, Any] = tool.get("poe", {}) if isinstance(tool, dict) else {}
    tasks = poe.get("tasks") if isinstance(poe, dict) else None
    return tasks if isinstance(tasks, dict) else None


def _definition_and_help(value: object) -> tuple[str, str | None]:
    if isinstance(value, dict):
        body = value.get("cmd") or value.get("shell") or value.get("script") or ""
        if isinstance(body, list):
            body = " ".join(str(x) for x in body)
        help_val = value.get("help")
        return str(body), (help_val if isinstance(help_val, str) else None)
    return str(value), None


class PoeProvider:
    prefix = "poe"

    def detect(self, cwd: Path) -> bool:
        return bool(_load_tasks(cwd))

    def discover(self, cwd: Path) -> list[Task]:
        tasks_table = _load_tasks(cwd)
        if not tasks_table:
            return []
        tasks: list[Task] = []
        for name, value in tasks_table.items():
            definition, help_text = _definition_and_help(value)
            tasks.append(
                Task(
                    name=name,
                    prefix=self.prefix,
                    argv_base=("poe", name),
                    description=help_text,
                    definition=definition,
                    source_file="pyproject.toml",
                )
            )
        return tasks
