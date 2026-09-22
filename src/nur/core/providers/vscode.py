from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["VsCodeProvider"]


log = logging.getLogger("nur")

_SOURCE_FILE = ".vscode/tasks.json"


def _strip_jsonc(text: str) -> str:  # noqa: C901, PLR0912
    """Remove JSONC comments and trailing commas without touching string contents."""
    result: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quoted:
            result.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            index += 1
            continue
        if character == '"':
            quoted = True
            result.append(character)
        elif character == "/" and following == "/":
            index = text.find("\n", index)
            if index < 0:
                break
            result.append("\n")
        elif character == "/" and following == "*":
            index = text.find("*/", index + 2)
            if index < 0:
                break
            index += 1
        elif character in "}]":
            whitespace_start = len(result)
            while whitespace_start and result[whitespace_start - 1].isspace():
                whitespace_start -= 1
            if whitespace_start and result[whitespace_start - 1] == ",":
                del result[whitespace_start - 1 :]
            result.append(character)
        else:
            result.append(character)
        index += 1
    return "".join(result)


def _platform_value(value: object) -> object:
    if not isinstance(value, dict):
        return value
    platform = {"darwin": "osx", "win32": "windows"}.get(sys.platform, "linux")
    return value.get(platform, value.get("value"))


def _command_and_args(entry: dict[str, object]) -> tuple[str, tuple[str, ...]] | None:
    command = _platform_value(entry.get("command"))
    raw_args = _platform_value(entry.get("args", []))
    if not isinstance(command, str) or not isinstance(raw_args, list):
        return None
    arguments: list[str] = []
    for argument in raw_args:
        value = argument.get("value") if isinstance(argument, dict) else argument
        if not isinstance(value, str):
            return None
        arguments.append(value)
    return command, tuple(arguments)


def _load_tasks(cwd: Path) -> list[dict[str, object]] | None:
    path = cwd / _SOURCE_FILE
    if not path.is_file():
        return None
    try:
        document = json.loads(_strip_jsonc(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
        return None
    if not isinstance(document, dict) or document.get("version") != "2.0.0":
        return None
    tasks = document.get("tasks")
    return tasks if isinstance(tasks, list) else None


class VsCodeProvider:
    """Task provider for VS Code's version 2.0.0 tasks configuration."""

    prefix = "vscode"

    def detect(self, cwd: Path) -> bool:
        """Check whether a supported VS Code tasks file exists."""
        return _load_tasks(cwd) is not None

    def discover(self, cwd: Path) -> list[Task]:
        """Discover directly runnable shell and process tasks."""
        entries = _load_tasks(cwd)
        if entries is None:
            return []
        tasks_by_label: dict[str, Task] = {}
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") not in {
                None,
                "shell",
                "process",
            }:
                continue
            label = entry.get("label")
            command_args = _command_and_args(entry)
            if not isinstance(label, str) or not label or command_args is None:
                continue
            command, arguments = command_args
            argv_base = (command, *arguments)
            detail = entry.get("detail")
            tasks_by_label[label] = Task(
                name=label,
                prefix=self.prefix,
                argv_base=argv_base,
                description=detail if isinstance(detail, str) else None,
                definition=" ".join(argv_base),
                source_file=_SOURCE_FILE,
            )
        return list(tasks_by_label.values())
