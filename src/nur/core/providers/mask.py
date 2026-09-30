from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._markdown import HEADING, LIST_ITEM_FENCE, scan

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MaskProvider", "parse_mask"]


log = logging.getLogger("nur")

SOURCE_FILE = "maskfile.md"

# CommonMark ends lines only at these; str.splitlines also splits on form
# feeds and Unicode separators, which would invent headings.
LINE_ENDING = re.compile(r"\r\n|\r|\n")
BLOCKQUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
# An indented code block: four spaces or a tab, where a paragraph cannot
# continue (after a blank line or another block).
INDENTED_CODE = re.compile(r"^(?: {4,}|\t| {1,3}\t)\S")
LIST_ITEM = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
THEMATIC_BREAK = re.compile(
    r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
# A setext heading's underline: `===` for level 1, `---` for level 2.
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
# List items that end a blockquote's paragraph instead of lazily continuing it.
PARAGRAPH_INTERRUPT = re.compile(r"^ {0,3}(?:[-*+]|1[.)])(?:[ \t]|$)")
# An inline link in a heading names the command by its text, as in mask.
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Outside Windows, mask skips these fences entirely, as though they were absent,
# so a command whose only script is one of them has no script to run.
WINDOWS_ONLY_EXECUTORS = frozenset({"powershell", "batch", "cmd"})


@dataclass
class _Command:
    level: int
    name: str = ""
    # Lines of the last blockquote, joined only when read: rebuilding the
    # description per line would be quadratic in a long blockquote. An empty
    # entry separates the quote's paragraphs.
    quote: list[str] = field(default_factory=list)
    # The last block's body, or None when mask cannot run it.
    script: str | None = None
    subcommands: list[_Command] = field(default_factory=list)

    @property
    def description(self) -> str | None:
        # mask keeps only a blockquote's last paragraph.
        last: list[str] = []
        paragraph: list[str] = []
        for part in self.quote:
            if part:
                paragraph.append(part)
            elif paragraph:
                last, paragraph = paragraph, []
        return " ".join(paragraph or last) or None


def _command_name(text: str) -> str:
    """Strip a heading's ``(required)`` and ``[optional]`` argument declarations."""
    text = LINK.sub(r"\1", text.strip())
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
    match = LIST_ITEM_FENCE.match(fence)
    info = match.group(2).strip() if match is not None else ""
    if not windows and info in WINDOWS_ONLY_EXECUTORS:
        return
    # Each block overwrites the last: mask runs the final one.
    # mask refuses to run a script without a language tag (the tag selects the
    # interpreter) or without any line; a script of blank lines still runs.
    runnable = bool(info) and bool(body)
    indent = match.start(1) if match is not None else 0
    command.script = _dedent(body, indent) if runnable else None


@dataclass
class _Reader:
    """Block state carried between the lines outside fenced code."""

    quote: list[str] = field(default_factory=list)
    # The lines of an open top-level paragraph, which a setext underline turns
    # into a heading.
    paragraph: list[str] = field(default_factory=list)
    # Whether the previous line was blank or ended a block, so that an indented
    # line starts a code block rather than continuing a paragraph.
    boundary: bool = True
    in_list: bool = False

    def reset(self) -> None:
        self.quote, self.paragraph, self.boundary, self.in_list = [], [], True, False

    def read(self, command: _Command, line: str) -> tuple[int, str] | None:
        """Read one line under *command*; return a heading's (level, text)."""
        if self.boundary and not self.in_list and INDENTED_CODE.match(line):
            # mask runs its last code block, and an indented one has no
            # language tag, so mask cannot run this command.
            command.script = None
            return None
        if (match := BLOCKQUOTE.match(line)) is not None:
            if not self.quote:
                # The last blockquote under a heading is its description.
                command.quote = self.quote
            self.quote.append(match.group(1).strip())
            self.paragraph, self.boundary = [], False
            return None
        heading = HEADING.match(line)
        if heading is None and self._continues_quote(line):
            self.quote.append(line.strip())
            return None
        self.quote = []
        after_boundary = self.boundary
        self.boundary = not line.strip() or heading is not None
        if heading is not None:
            self.paragraph = []
            return len(heading.group(1)), heading.group(2) or ""
        return self._read_text(line, after_boundary=after_boundary)

    def _continues_quote(self, line: str) -> bool:
        """Whether *line* lazily continues the blockquote's paragraph."""
        return (
            bool(self.quote and self.quote[-1] and line.strip())
            and THEMATIC_BREAK.match(line) is None
            and PARAGRAPH_INTERRUPT.match(line) is None
        )

    def _read_text(self, line: str, *, after_boundary: bool) -> tuple[int, str] | None:
        if not line.strip():
            self.paragraph = []
            return None
        if self.paragraph and (underline := SETEXT_UNDERLINE.match(line)):
            text = " ".join(self.paragraph)
            self.paragraph, self.boundary = [], True
            return (1 if underline.group(1)[0] == "=" else 2), text
        if THEMATIC_BREAK.match(line):
            self.paragraph, self.boundary, self.in_list = [], True, False
            return None
        if LIST_ITEM.match(line):
            self.paragraph, self.in_list = [], True
            return None
        if self.in_list and (not after_boundary or line.startswith((" ", "\t"))):
            return None  # the list item continues
        self.in_list = False
        self.paragraph.append(line.strip())
        return None


def _flat_commands(text: str, *, windows: bool) -> list[_Command]:
    """Collect one command per heading, in file order, as mask's parser does.

    The first command is always the level-1 root (the file's title, or an
    unnamed root when there is none). A second level-1 heading ends the command
    list once any command has been seen -- mask stops parsing there.
    """
    # Blank out HTML blocks first so a commented-out command, heading and
    # fence alike, reads as empty lines.
    raw = LINE_ENDING.split(text)
    if not raw[-1]:
        raw.pop()
    fences, hidden = scan(raw, list_items=True, html=True)
    lines = ["" if index in hidden else line for index, line in enumerate(raw)]
    code = {index for block in fences for index in range(block.open, block.end)}
    openings = {block.open: block.body_end for block in fences}

    commands: list[_Command] = []
    current = _Command(level=1)
    reader = _Reader()
    for index, line in enumerate(lines):
        if index in openings:
            _take_script(
                current, line, lines[index + 1 : openings[index]], windows=windows
            )
        if index in code:
            reader.reset()
            continue
        if (heading := reader.read(current, line)) is None:
            continue
        level, text = heading
        if level > 1:
            commands.append(current)
        elif commands:
            break
        current = _Command(level=level, name=_command_name(text))
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
    for command in commands[1:]:
        if command.level > current.level:
            if command.name.startswith(current.name):
                command.name = command.name[len(current.name) :].strip()
            current.subcommands.append(command)
        elif command.level == current.level:
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
        if command.script is not None:
            tasks.append(
                Task(
                    name=" ".join(path),
                    prefix="mask",
                    argv_base=("mask", *path),
                    description=command.description,
                    definition=command.script,
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
    # mask resolves a repeated command to its last definition, so a later
    # duplicate replaces the earlier task rather than listing both. Commands
    # that only render alike (`## deploy prod` vs `prod` under `## deploy`)
    # run differently, so both stay.
    tasks = _tasks(root.subcommands, (), source_file)
    unique = {task.argv_base: task for task in tasks}
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
