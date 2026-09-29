from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._markdown import (
    FENCE,
    HEADING,
    code_lines,
    comment_lines,
    fence_blocks,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MaskProvider", "parse_mask"]


log = logging.getLogger("nur")

SOURCE_FILE = "maskfile.md"

BLOCKQUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
# Outside Windows, mask skips these fences entirely, as though they were absent,
# so a command whose only script is one of them has no script to run.
WINDOWS_ONLY_EXECUTORS = frozenset({"powershell", "batch", "cmd"})


@dataclass
class _Command:
    level: int
    name: str = ""
    # Lines of the last blockquote, joined only when read: rebuilding the
    # description per line would be quadratic in a long blockquote.
    quote: list[str] = field(default_factory=list)
    executor: str = ""
    source: str = ""
    # Whether the script block has any line at all: mask runs a script of blank
    # lines (its source is a bare newline) but not one with no lines.
    has_body: bool = False
    subcommands: list[_Command] = field(default_factory=list)

    @property
    def description(self) -> str | None:
        return " ".join(part for part in self.quote if part) or None

    @property
    def runnable(self) -> bool:
        # mask refuses to run a command whose script lacks a body or a language
        # tag (the tag selects the interpreter), so neither is listed.
        return bool(self.executor) and self.has_body


def _command_name(text: str) -> str:
    """Strip a heading's ``(required)`` and ``[optional]`` argument declarations."""
    text = text.strip()
    # Drop an ATX heading's optional closing sequence: hashes that fill the
    # heading or follow whitespace. Done without a regex, which backtracks
    # quadratically over a long whitespace run in a hostile heading.
    head = text.rstrip("#")
    if head != text and (not head or head[-1].isspace()):
        text = head.rstrip()
    return re.split(r"[(\[]", text, maxsplit=1)[0].strip()


def _dedent(body: list[str], indent: int) -> str:
    """Join a fence's body, dropping up to the fence's own indent from each line."""
    return "\n".join(
        line[min(indent, len(line) - len(line.lstrip(" "))) :] for line in body
    )


def _take_script(
    command: _Command, fence: str, body: list[str], *, windows: bool
) -> None:
    """Make the block opened by the *fence* line *command*'s script."""
    match = FENCE.match(fence)
    info = match.group(2).strip() if match is not None else ""
    if not windows and info in WINDOWS_ONLY_EXECUTORS:
        return
    # Each block overwrites the last: mask runs the final one.
    command.executor = info
    command.source = _dedent(body, len(fence) - len(fence.lstrip(" ")))
    command.has_body = bool(body)


def _flat_commands(text: str, *, windows: bool) -> list[_Command]:
    """Collect one command per heading, in file order, as mask's parser does.

    The first command is always the level-1 root (the file's title, or an
    unnamed root when there is none). A second level-1 heading ends the command
    list once any command has been seen -- mask stops parsing there.
    """
    # Blank out HTML comments first so a commented-out command, heading and
    # fence alike, reads as empty lines.
    raw = text.splitlines()
    comments = comment_lines(raw)
    lines = ["" if index in comments else line for index, line in enumerate(raw)]
    blocks = fence_blocks(lines)
    code = code_lines(lines, blocks)
    openings = dict(blocks)

    commands: list[_Command] = []
    current = _Command(level=1)
    quote: list[str] = []
    for index, line in enumerate(lines):
        if index in openings:
            _take_script(
                current, line, lines[index + 1 : openings[index]], windows=windows
            )
        if index in code:
            quote = []
            continue
        if (match := BLOCKQUOTE.match(line)) is not None:
            if not quote:
                # The last blockquote under a heading is its description.
                current.quote = quote
            quote.append(match.group(1).strip())
            continue
        heading = HEADING.match(line)
        if heading is None and quote and quote[-1] and line.strip():
            # A lazy continuation line extends the blockquote's paragraph.
            quote.append(line.strip())
            continue
        quote = []
        if heading is None:
            continue
        level = len(heading.group(1))
        if level > 1:
            commands.append(current)
        elif commands:
            break
        current = _Command(level=level, name=_command_name(heading.group(2) or ""))
    commands.append(current)
    return commands


def _treeify(commands: list[_Command]) -> list[_Command]:
    """Nest a flat command list into mask's command tree.

    A port of mask's ``treeify_commands``, quirks included, because the tree is
    what mask's CLI accepts: a deeper heading after a command is its
    subcommand, with the parent's name stripped as a plain string prefix
    (``### test lint`` under ``## test`` is ``lint``), and a heading shallower
    than the first in the list is dropped.
    """
    tree: list[_Command] = []
    current = commands[0]
    for index, command in enumerate(commands):
        if command.level > current.level:
            if command.name.startswith(current.name):
                command.name = command.name[len(current.name) :].strip()
            current.subcommands.append(command)
        elif command.level == current.level and index > 0:
            tree.append(current)
            current = command
    tree.append(current)
    for command in tree:
        if command.subcommands:
            command.subcommands = _treeify(command.subcommands)
    return tree


def _tasks(
    commands: list[_Command], parents: tuple[str, ...], source_file: str
) -> list[Task]:
    tasks: list[Task] = []
    for command in commands:
        if not command.name:
            log.debug("nur: skipping unnamed mask command under %r", parents)
            continue
        path = (*parents, command.name)
        if command.runnable:
            tasks.append(
                Task(
                    name=" ".join(path),
                    prefix="mask",
                    argv_base=("mask", *path),
                    description=command.description,
                    definition=command.source,
                    source_file=source_file,
                )
            )
        tasks.extend(_tasks(command.subcommands, path, source_file))
    return tasks


def parse_mask(
    text: str, source_file: str = SOURCE_FILE, *, windows: bool = os.name == "nt"
) -> list[Task]:
    """Parse mask commands out of a maskfile *text* without executing anything.

    Mirrors mask's own parser (https://github.com/jacobdeichert/mask): headings
    are commands, nested headings are subcommands, the last fenced block under
    a heading is its script, and the last blockquote its description. A
    subcommand is named by its path (``test lint``) and runs as
    ``mask test lint``. *windows* selects whether ``powershell``/``batch``/
    ``cmd`` blocks count, as they do only in mask's Windows build.
    """
    root = _treeify(_flat_commands(text, windows=windows))[0]
    # mask resolves a repeated command name to its last definition, so a later
    # duplicate replaces the earlier task rather than listing both.
    unique = {task.name: task for task in _tasks(root.subcommands, (), source_file)}
    return list(unique.values())


class MaskProvider:
    prefix = "mask"

    def detect(self, cwd: Path) -> bool:
        return (cwd / SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / SOURCE_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping %s (%s)", SOURCE_FILE, exc)
            return []
        return parse_mask(text, SOURCE_FILE)
