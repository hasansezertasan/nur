from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PdmProvider"]


log = logging.getLogger("nur")


def _load_scripts(cwd: Path) -> dict[str, object] | None:
    """Return the [tool.pdm.scripts] table, or None if absent/unreadable."""
    path = cwd / "pyproject.toml"
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping pyproject.toml (%s)", exc)
        return None
    tool = data.get("tool", {})
    pdm: dict[str, Any] = tool.get("pdm", {}) if isinstance(tool, dict) else {}
    scripts = pdm.get("scripts") if isinstance(pdm, dict) else None
    return scripts if isinstance(scripts, dict) else None


def _definition_and_help(value: object) -> tuple[str, str | None]:
    if isinstance(value, dict):
        body = value.get("cmd") or value.get("shell") or value.get("call") or ""
        if isinstance(body, list):
            body = " ".join(str(x) for x in body)
        help_val = value.get("help")
        return str(body), (help_val if isinstance(help_val, str) else None)
    return str(value), None


class PdmProvider:
    prefix = "pdm"

    def detect(self, cwd: Path) -> bool:
        scripts = _load_scripts(cwd)
        return bool(scripts)

    def discover(self, cwd: Path) -> list[Task]:
        scripts = _load_scripts(cwd)
        if not scripts:
            return []
        tasks: list[Task] = []
        for name, value in scripts.items():
            if name == "_":  # PDM reserved global-options key
                continue
            definition, help_text = _definition_and_help(value)
            tasks.append(
                Task(
                    name=name,
                    prefix=self.prefix,
                    argv_base=("pdm", "run", name),
                    description=help_text,
                    definition=definition,
                    source_file="pyproject.toml",
                )
            )
        return tasks
