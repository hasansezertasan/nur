from __future__ import annotations

import json
import logging
import subprocess
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["JustProvider", "parse_dump"]


log = logging.getLogger("nur")


def parse_dump(text: str, source_file: str = "justfile") -> list[Task]:
    data = json.loads(text)
    tasks: list[Task] = []
    recipes: dict[str, Any] = data.get("recipes") or {}
    for name, recipe in recipes.items():
        tasks.append(
            Task(
                name=name,
                prefix="just",
                argv_base=("just", name),
                description=recipe.get("doc"),
                source_file=source_file,
            )
        )
    return tasks


class JustProvider:
    prefix = "just"

    def _source_file(self, cwd: Path) -> str:
        return "justfile" if (cwd / "justfile").is_file() else "Justfile"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "justfile").is_file() or (cwd / "Justfile").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            result = subprocess.run(
                ["just", "--dump", "--dump-format", "json"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
        except OSError:
            log.warning("nur: 'just' not installed; skipping justfile tasks")
            return []
        except subprocess.TimeoutExpired:
            log.warning("nur: 'just --dump' timed out; skipping justfile tasks")
            return []
        try:
            return parse_dump(result.stdout, self._source_file(cwd))
        except (json.JSONDecodeError, AttributeError) as exc:
            log.warning("nur: skipping justfile (%s)", exc)
            return []
