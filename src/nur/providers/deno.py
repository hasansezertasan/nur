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


def _copy_string(text: str, start: int, out: list[str]) -> int:
    """Copy a string literal (from its opening quote); return the index after it."""
    out.append(text[start])
    i, n = start + 1, len(text)
    while i < n:
        char = text[i]
        out.append(char)
        if char == "\\" and i + 1 < n:  # copy the escaped char verbatim
            out.append(text[i + 1])
            i += 2
            continue
        i += 1
        if char == '"':
            break
    return i


def _skip_comment(text: str, i: int) -> int | None:
    """If a comment starts at ``i``, return the index past it; otherwise None."""
    if text.startswith("//", i):
        end = text.find("\n", i + 2)
        return len(text) if end == -1 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        if end == -1:  # an unterminated block comment is malformed JSONC
            msg = "unterminated block comment"
            raise ValueError(msg)
        return end + 2
    return None


def _drop_trailing_comma(out: list[str]) -> None:
    """Remove a trailing comma (and any whitespace) before a closing ``}``/``]``."""
    j = len(out) - 1
    while j >= 0 and out[j] in " \t\r\n":
        j -= 1
    if j >= 0 and out[j] == ",":
        del out[j:]


def _strip_jsonc(text: str) -> str:
    """Remove ``//``/``/* */`` comments and trailing commas from JSONC text.

    String-aware: comment markers and commas inside string literals are left
    untouched, so stdlib ``json.loads`` can parse the result.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            i = _copy_string(text, i, out)
            continue
        skipped = _skip_comment(text, i)
        if skipped is not None:
            i = skipped
            continue
        if char in "}]":
            _drop_trailing_comma(out)
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        log.warning("nur: skipping %s (%s)", name, exc)
        return None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    return (name, tasks) if isinstance(tasks, dict) else None


def _definition_and_help(value: str | dict[str, object]) -> tuple[str, str | None]:
    # A task is either a command string, or an object with a `command` field
    # plus an optional `description`.
    if isinstance(value, dict):
        command = value.get("command")
        desc = value.get("description")
        return (
            command if isinstance(command, str) else "",
            desc if isinstance(desc, str) else None,
        )
    return value, None


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
            # A Deno task value is a command string or an object; skip anything
            # else (numbers, arrays, booleans) as malformed rather than
            # stringifying it into a bogus task.
            if not isinstance(value, (str, dict)):
                continue
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
