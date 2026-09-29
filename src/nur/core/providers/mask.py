from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._markdown import FENCE, HEADING, code_lines, fence_blocks

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MaskProvider", "parse_mask"]


log = logging.getLogger("nur")

SOURCE_FILE = "maskfile.md"

# An ATX heading's optional closing sequence: hashes preceded by whitespace (or
# filling the whole heading), which markdown drops from the heading text.
CLOSING_HASHES = re.compile(r"(?:^|\s+)#+\s*$")
BLOCKQUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
# Outside Windows, mask skips these fences entirely, as though they were absent,
# so a command whose only script is one of them has no script to run.
WINDOWS_ONLY_EXECUTORS = frozenset({"powershell", "batch", "cmd"})


@dataclass
class _Command:
    level: int
    name: str = ""
    description: str | None = None
    executor: str = ""
    source: str | None = None
    subcommands: list[_Command] = field(default_factory=list)

    @property
    def runnable(self) -> bool:
        # mask refuses to run a command whose script lacks a body or a language
        # tag (the tag selects the interpreter), so neither is listed.
        return bool(self.executor) and bool(self.source)


def _command_name(text: str) -> str:
    """Strip a heading's ``(required)`` and ``[optional]`` argument declarations."""
    text = CLOSING_HASHES.sub("", text.strip())
    return re.split(r"[(\[]", text, maxsplit=1)[0].strip()


def _flat_commands(text: str, *, windows: bool) -> list[_Command]:
    """Collect one command per heading, in file order, as mask's parser does.

    The first command is always the level-1 root (the file's title, or an
    unnamed root when there is none). A second level-1 heading ends the command
    list once any command has been seen -- mask stops parsing there.
    """
    lines = text.splitlines()
    blocks = fence_blocks(lines)
    code = code_lines(lines, blocks)
    openings = dict(blocks)

    commands: list[_Command] = []
    current = _Command(level=1)
    quote: list[str] = []
    for index, line in enumerate(lines):
        if index in openings and (fence := FENCE.match(line)) is not None:
            info = fence.group(2).strip()
            if windows or info not in WINDOWS_ONLY_EXECUTORS:
                # Each block overwrites the last: mask runs the final one.
                current.executor = info
                current.source = "\n".join(lines[index + 1 : openings[index]])
        if index in code:
            quote = []
            continue
        if (match := BLOCKQUOTE.match(line)) is not None:
            quote.append(match.group(1).strip())
            # The last blockquote under a heading is its description.
            current.description = " ".join(part for part in quote if part) or None
            continue
        quote = []
        if (heading := HEADING.match(line)) is None:
            continue
        level = len(heading.group(1))
        if level > 1:
            commands.append(current)
        elif commands:
            break
        current = _Command(level=level, name=_command_name(heading.group(2)))
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
                    definition=command.source or "",
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
    return _tasks(root.subcommands, (), source_file)


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
