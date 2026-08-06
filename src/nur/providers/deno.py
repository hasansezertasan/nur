from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["DenoProvider"]


log = logging.getLogger("nur")

# Deno reads deno.json first, then deno.jsonc. Only the first existing file is
# parsed (single-file discovery, like the mise provider).
CONFIG_FILES = ("deno.json", "deno.jsonc")


def _strip_jsonc(text: str) -> str:
    """Remove ``//``/``/* */`` comments and trailing commas from JSONC text.

    String-aware: comment markers and commas inside string literals are left
    untouched, so ``tomllib``-free stdlib ``json.loads`` can parse the result.
    """
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        char = text[i]
        if in_string:
            out.append(char)
            if char == "\\" and i + 1 < n:  # copy the escaped char verbatim
                out.append(text[i + 1])
                i += 2
                continue
            if char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
        elif char == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif char == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        elif char in "}]":
            # Drop a trailing comma (and any whitespace) before the close.
            j = len(out) - 1
            while j >= 0 and out[j] in " \t\r\n":
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j:]
            out.append(char)
        else:
            out.append(char)
        i += 1
    return "".join(out)


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
        data = json.loads(_strip_jsonc((cwd / name).read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", name, exc)
        return None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return (name, tasks) if isinstance(tasks, dict) else None


def _definition_and_help(value: object) -> tuple[str, str | None]:
    # A task is either a command string, or an object with a `command` field
    # plus an optional `description`.
    if isinstance(value, dict):
        command = value.get("command")
        desc = value.get("description")
        return (
            command if isinstance(command, str) else "",
            desc if isinstance(desc, str) else None,
        )
    return str(value), None


class DenoProvider:
    prefix = "deno"

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
                    argv_base=("deno", "task", name),
                    description=help_text,
                    definition=definition,
                    source_file=source_file,
                )
            )
        return tasks
