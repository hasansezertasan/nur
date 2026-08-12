from __future__ import annotations

import logging
import tomllib
from typing import TYPE_CHECKING, Any

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["CargoMakeProvider"]


log = logging.getLogger("nur")


def _load_tasks(cwd: Path) -> dict[str, object] | None:
    path = cwd / "Makefile.toml"
    # Absence is the common case (the provider is globally registered): stay
    # silent, mirroring the mise provider. Only warn on a present-but-unreadable
    # or malformed file.
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("nur: skipping Makefile.toml (%s)", exc)
        return None
    tasks = data.get("tasks")
    return tasks if isinstance(tasks, dict) else None


def _script_body(script: object) -> str:
    # A cargo-make `script` may be a single/multi-line string, a list of shell
    # lines, an external-file table (`{ file = "..." }`), or pre/main/post
    # section tables. Everything is flattened to a single-line preview.
    if isinstance(script, list):
        return " && ".join(str(line) for line in script)
    if isinstance(script, str):
        lines = [line.strip() for line in script.splitlines() if line.strip()]
        return " && ".join(lines)
    if isinstance(script, dict):
        file_val = script.get("file")
        if isinstance(file_val, str):
            return f"file: {file_val}"
        parts = [
            _script_body(script[key])
            for key in ("pre", "main", "post")
            if key in script
        ]
        return " && ".join(part for part in parts if part)
    return ""


def _definition(value: dict[str, Any]) -> str:
    # cargo-make tasks run either a `command` (+ `args`) or a `script`; a task
    # may also be composite (only `dependencies`), in which case there is no
    # local command to show.
    command = value.get("command")
    if isinstance(command, str) and command:
        args = value.get("args")
        if isinstance(args, list):
            return " ".join([command, *(str(a) for a in args)])
        return command
    return _script_body(value.get("script"))


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
            # cargo-make hides `private = true` from its listing and treats
            # `disabled = true` tasks as no-ops; skip both. Task `extend`,
            # `alias`, and `condition` semantics are intentionally not resolved
            # (see issue #76): nur delegates all execution to `cargo make <name>`.
            if value.get("private") is True or value.get("disabled") is True:
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
