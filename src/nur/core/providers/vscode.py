from __future__ import annotations

import json
import logging
import os
import re
import sys
from typing import TYPE_CHECKING, cast

from nur.core.models import Task
from nur.core.shell import quote

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["VsCodeProvider"]


log = logging.getLogger("nur")

_SOURCE_FILE = ".vscode/tasks.json"
_VARIABLE = re.compile(r"\$\{([^}]*)\}")


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
            end = text.find("*/", index + 2)
            if end < 0:
                raise ValueError("unterminated block comment")  # noqa: EM101, TRY003
            # Blank the comment rather than drop it so adjacent tokens stay apart.
            result.append(" " + "\n" * text.count("\n", index, end))
            index = end + 1
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


def _platform_entry(entry: dict[str, object]) -> dict[str, object]:
    platform = {"darwin": "osx", "win32": "windows"}.get(sys.platform, "linux")
    override = entry.get(platform)
    return {**entry, **override} if isinstance(override, dict) else entry


def _resolve_variables(value: str, cwd: Path) -> str | None:
    """Substitute the VS Code variables nur can know; ``None`` if any remain."""
    unresolved: list[str] = []
    # VS Code substitutes an absolute path, so a relative cwd must not leak in.
    workspace = cwd.resolve()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in {"workspaceFolder", "workspaceRoot"}:
            return str(workspace)
        if name == "workspaceFolderBasename":
            return workspace.name
        if name in {"pathSeparator", "/"}:
            return os.sep
        if name.startswith("env:"):
            return os.environ.get(name.removeprefix("env:"), "")
        unresolved.append(name)
        return match.group(0)

    resolved = _VARIABLE.sub(replace, value)
    return None if unresolved else resolved


def _supported_options(options: object, cwd: Path) -> bool:
    """Accept only options nur can honor: none, or a cwd equal to the project."""
    if options is None:
        return True
    if not isinstance(options, dict) or options.get("env") or "shell" in options:
        return False
    work_dir = options.get("cwd")
    if work_dir is None:
        return True
    resolved = _resolve_variables(work_dir, cwd) if isinstance(work_dir, str) else None
    return resolved is not None and (cwd / resolved).resolve() == cwd.resolve()


def _value(item: object) -> object:
    # Commands and args may be plain strings or {"value": ..., "quoting": ...}.
    return item.get("value") if isinstance(item, dict) else item


def _effective_options(root: object, task: object) -> object:
    """Layer task options over document options, as VS Code does."""
    if root is None or task is None:
        return task if root is None else root
    if not isinstance(root, dict) or not isinstance(task, dict):
        return task  # Malformed options are rejected by _supported_options.
    merged = {**root, **task}
    # env is merged key by key, so any document-level variable still applies.
    merged["env"] = root.get("env") or task.get("env")
    return merged


def _with_global_command(
    entry: dict[str, object], root: dict[str, object]
) -> dict[str, object]:
    """Fill a commandless task from the document's command, as VS Code does.

    The task takes the global command and runs it with the global args, the
    task name (only when ``suppressTaskName`` is false), then its own args. A
    task with its own command keeps its own args only, and a commandless task
    that declares ``dependsOn`` inherits nothing.
    """
    if "command" in entry or "dependsOn" in entry or "command" not in root:
        return entry
    global_args, task_args = root.get("args", []), entry.get("args", [])
    if not isinstance(global_args, list) or not isinstance(task_args, list):
        return {**entry, "args": None}  # Rejected by _command_and_args.
    label = entry.get("label")
    suppress = entry.get("suppressTaskName", root.get("suppressTaskName", True))
    selector = entry.get("taskSelector", root.get("taskSelector", ""))
    name_args = [f"{selector}{label}"] if not suppress and label else []
    args = [*global_args, *name_args, *task_args]
    return {**entry, "command": root["command"], "args": args}


def _command_and_args(
    entry: dict[str, object], cwd: Path
) -> tuple[str, tuple[str, ...]] | None:
    raw_args = entry.get("args", [])
    if not isinstance(raw_args, list):
        return None
    values = [_value(entry.get("command")), *map(_value, raw_args)]
    if not all(isinstance(value, str) for value in values):
        return None
    resolved = [_resolve_variables(value, cwd) for value in cast("list[str]", values)]
    if None in resolved:
        return None
    command, *arguments = cast("list[str]", resolved)
    return command, tuple(arguments)


def _load_document(cwd: Path) -> dict[str, object] | None:
    path = cwd / _SOURCE_FILE
    if not path.is_file():
        return None
    try:
        document = json.loads(_strip_jsonc(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
        return None
    if (
        not isinstance(document, dict)
        or document.get("version") != "2.0.0"
        or not isinstance(document.get("tasks"), list)
    ):
        return None
    return document


class VsCodeProvider:
    """Task provider for VS Code's version 2.0.0 tasks configuration."""

    prefix = "vscode"

    def detect(self, cwd: Path) -> bool:
        """Check whether a supported VS Code tasks file exists."""
        return _load_document(cwd) is not None

    def discover(self, cwd: Path) -> list[Task]:
        """Discover shell and process tasks nur can run as VS Code would."""
        document = _load_document(cwd)
        if document is None:
            return []
        root = _platform_entry(document)
        tasks_by_label: dict[str, Task] = {}
        for raw_entry in cast("list[object]", document["tasks"]):
            if not isinstance(raw_entry, dict):
                continue
            entry = _with_global_command(_platform_entry(raw_entry), root)
            if "type" not in entry and "type" in root:
                entry["type"] = root["type"]
            entry["options"] = _effective_options(
                root.get("options"), entry.get("options")
            )
            task = self._task(entry, cwd)
            if task is not None:
                tasks_by_label[task.name] = task
        return list(tasks_by_label.values())

    def _task(self, entry: dict[str, object], cwd: Path) -> Task | None:
        # Tasks with prerequisites are skipped: nur runs no dependency graph.
        if (
            entry.get("type") not in {None, "shell", "process"}
            or entry.get("hide") is True
            or entry.get("dependsOn")
            or not _supported_options(entry.get("options"), cwd)
        ):
            return None
        label = entry.get("label")
        command_args = _command_and_args(entry, cwd)
        if not isinstance(label, str) or not label or command_args is None:
            return None
        command, arguments = command_args
        # Like VS Code, a task whose type resolves to nothing runs as a process.
        run_in_shell = entry.get("type") == "shell"
        if run_in_shell and isinstance(entry.get("command"), dict):
            # The object form marks the command as a literal token to quote; it
            # still runs through the shell, so builtins keep working.
            command = quote(command)
        argv_base = (command, *arguments)
        detail = entry.get("detail")
        return Task(
            name=label,
            prefix=self.prefix,
            argv_base=argv_base,
            description=detail if isinstance(detail, str) else None,
            definition=" ".join(argv_base),
            source_file=_SOURCE_FILE,
            run_in_shell=run_in_shell,
        )
