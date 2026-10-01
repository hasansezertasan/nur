from __future__ import annotations

import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._inline import (
    DEFINITION,
    DEFINITION_TARGET,
    heading_text,
    normalize_label,
)
from nur.core.providers._markdown import (
    HEADING,
    SETEXT_UNDERLINE,
    THEMATIC_BREAK,
    Fence,
    container_content,
    container_line,
    indent_width,
    list_item_content,
    scan,
    strip_columns,
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["MaskProvider", "parse_mask"]


log = logging.getLogger("nur")

SOURCE_FILE = "maskfile.md"

# mask ends lines only at these (not at a lone carriage return, unlike
# CommonMark); str.splitlines also splits on form feeds and Unicode
# separators, which would invent headings.
LINE_ENDING = re.compile(r"\r?\n")
BLOCKQUOTE = re.compile(r"^ {0,3}>\s?(.*)$")
# A list item's marker and the one to four spaces setting its content column.
# The indentation, in columns, that makes a line an indented code block.
INDENTED_CODE = 4
# List items that end a blockquote's paragraph instead of lazily continuing it.
PARAGRAPH_INTERRUPT = re.compile(r"^ {0,3}(?:[-*]|1[.)])(?:[ \t]|$)")
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


def _command_name(text: str, labels: frozenset[str] = frozenset()) -> str:
    """Strip a heading's ``(required)`` and ``[optional]`` argument declarations."""
    text = text.strip()
    # Drop an ATX heading's optional closing sequence: hashes that fill the
    # heading or follow an ASCII space (pulldown-cmark 0.5 keeps a tab).
    # Done without a regex, which backtracks
    # quadratically over a long whitespace run in a hostile heading.
    head = text.rstrip("#")
    if head != text and (not head or head[-1] == " "):
        text = head.rstrip()
    return re.split(r"[(\[]", heading_text(text, labels), maxsplit=1)[0].strip()


def _take_script(
    command: _Command, fence: Fence, body: list[str], *, windows: bool
) -> None:
    """Make the *fence*'s *body* lines *command*'s script."""
    info = fence.info
    if not windows and info in WINDOWS_ONLY_EXECUTORS:
        return
    # Each block overwrites the last: mask runs the final one.
    # mask refuses to run a script without a language tag (the tag selects the
    # interpreter) or without any line; a script of blank lines still runs.
    runnable = bool(info) and bool(body)
    # Each body line loses up to the fence's own indentation, by columns.
    script = "\n".join(strip_columns(line, fence.indent) for line in body)
    command.script = script if runnable else None


def _list_item(line: str, *, paragraph: bool = False) -> tuple[int, str] | None:
    """Return a list item's content column and content, if *line* opens one."""
    if (item := list_item_content(line, paragraph=paragraph)) is None:
        return None
    return item[0], line[item[1] :]


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
    # The content column of the list item being continued, if any.
    list_indent: int | None = None

    def reset(self, *, list_indent: int | None) -> None:
        self.quote, self.paragraph, self.boundary = [], [], True
        self.list_indent = list_indent

    def fence_list_indent(self, line: str) -> int | None:
        """Return the list content column a fence opening on *line* sits in."""
        if (item := _list_item(line)) is not None:
            return item[0]
        if self.list_indent is not None and indent_width(line) >= self.list_indent:
            return self.list_indent
        return None

    def _in_item(self, line: str) -> bool:
        """Whether *line* is indented into the open list item's content."""
        return self.list_indent is not None and indent_width(line) >= self.list_indent

    def read(self, command: _Command, line: str) -> tuple[int, str] | None:
        """Read one line under *command*; return a heading's (level, text)."""
        # Inside a list item, block structure is read relative to its content.
        inside = self._in_item(line) and bool(line.strip())
        inner = strip_columns(line, self.list_indent or 0) if inside else line
        if self.boundary and inner.strip() and indent_width(inner) >= INDENTED_CODE:
            # An indented code block. mask runs its last code block, and this
            # one has no language tag, so mask cannot run this command.
            command.script = None
            return None
        if (match := BLOCKQUOTE.match(inner)) is not None:
            return self._read_quote(command, match.group(1))
        heading = HEADING.match(inner)
        if heading is None and self._continues_quote(inner):
            self.quote.append(inner.strip())
            return None
        self.quote = []
        if heading is not None:
            return self._heading(heading)
        after_boundary = self.boundary
        self.boundary = not line.strip()
        offset = (self.list_indent or 0) if inside else 0
        return self._read_text(line, inner, offset, after_boundary=after_boundary)

    def _read_quote(self, command: _Command, content: str) -> tuple[int, str] | None:
        if (heading := HEADING.match(content)) is not None:
            return self._heading(heading)
        if (
            self.quote
            and self.quote[-1]
            and (underline := SETEXT_UNDERLINE.match(content))
        ):
            # The quote's open paragraph becomes a setext heading.
            start = len(self.quote)
            while start and self.quote[start - 1]:
                start -= 1
            text = " ".join(self.quote[start:])
            del self.quote[start:]
            self.paragraph, self.boundary = [], True
            return (1 if underline.group(1)[0] == "=" else 2), text
        if not self.quote:
            # The last blockquote under a heading is its description.
            command.quote = self.quote
        self.quote.append(content.strip())
        self.paragraph, self.boundary = [], False
        return None

    def _read_list_item(self, indent: int, content: str) -> tuple[int, str] | None:
        self.list_indent = indent
        # A heading may sit on the item's own line: `- ## build`. Otherwise
        # its text opens a paragraph, which an underline can make a heading.
        if (heading := HEADING.match(content)) is not None:
            return self._heading(heading)
        self.paragraph = [content.strip()] if content.strip() else []
        return None

    def _heading(self, heading: re.Match[str]) -> tuple[int, str]:
        self.quote, self.paragraph, self.boundary = [], [], True
        return len(heading.group(1)), heading.group(2) or ""

    def _continues_quote(self, line: str) -> bool:
        """Whether *line* lazily continues the blockquote's paragraph."""
        return (
            bool(self.quote and self.quote[-1] and line.strip())
            and THEMATIC_BREAK.match(line) is None
            and PARAGRAPH_INTERRUPT.match(line) is None
        )

    def _read_text(
        self, line: str, inner: str, offset: int, *, after_boundary: bool
    ) -> tuple[int, str] | None:
        """Read a line of text.

        *inner* is the line within the open list item, if any, whose content
        starts *offset* columns in.
        """
        if not line.strip():
            self.paragraph = []
            return None
        # Inside a list item, an underline must be indented into its content;
        # an unindented one ends the list instead.
        in_item = self.list_indent is None or self._in_item(line)
        if self.paragraph and in_item and (underline := SETEXT_UNDERLINE.match(inner)):
            text = " ".join(self.paragraph)
            self.paragraph, self.boundary = [], True
            return (1 if underline.group(1)[0] == "=" else 2), text
        thematic = THEMATIC_BREAK.match(inner) is not None
        if thematic or (not self.paragraph and DEFINITION.match(inner)):
            # A thematic break outside an item ends the list; a link reference
            # definition is not paragraph text, so neither becomes a heading.
            if thematic:
                self.paragraph, self.boundary = [], True
                self.list_indent = self.list_indent if offset else None
            return None
        if (item := _list_item(inner, paragraph=bool(self.paragraph))) is not None:
            return self._read_list_item(offset + item[0], item[1])
        if self.list_indent is not None and (
            not after_boundary or indent_width(line) >= self.list_indent
        ):
            # The list item continues: its paragraph, or a new one after a gap.
            if after_boundary:
                self.paragraph = []
            self.paragraph.append(line.strip())
            return None
        self.list_indent = None
        self.paragraph.append(line.strip())
        return None


def _valid_definition_target(target: str) -> bool:
    """Whether mask accepts a reference destination and its optional title."""
    match = DEFINITION_TARGET.match(target)
    if match is None:
        return False
    destination = match.group(1)
    if destination.startswith("<"):
        return True
    # pulldown-cmark 0.5 accepts unmatched opening parentheses in reference
    # destinations, unlike current CommonMark, but rejects unmatched closers.
    depth = 0
    escaped = False
    for char in destination:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            if not depth:
                return False
            depth -= 1
    return True


def _definition_labels(lines: list[str], code: set[int]) -> frozenset[str]:
    """Collect the labels of the link reference definitions outside code.

    Definitions may follow the headings that use them. Each needs exactly a
    destination and an optional title, after its colon or on the next line:
    `[build]:` alone, or with trailing text, is not a definition.
    """
    labels: set[str] = set()
    containers: tuple[int | None, ...] = ()
    for index, line in enumerate(lines):
        if index in code:
            continue
        # Reuse the block scanner's container rules, including nested markers
        # and list continuation indentation. Code and HTML remain excluded.
        inner = container_line(containers, line)
        if inner is None:
            containers = ()
            inner = line
        content, opened = container_content(inner)
        containers += opened
        if (match := DEFINITION.match(content)) is None:
            continue
        target = content[match.end() :]
        if not target.strip() and index + 1 < len(lines) and index + 1 not in code:
            target = container_line(containers, lines[index + 1]) or ""
        if _valid_definition_target(target):
            labels.add(normalize_label(match.group(1)))
    return frozenset(labels)


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
    openings = {block.open: block for block in fences}
    labels = _definition_labels(lines, code)

    commands: list[_Command] = []
    current = _Command(level=1)
    reader = _Reader()
    fence_list: int | None = None
    for index, line in enumerate(lines):
        if index in openings:
            fence = openings[index]
            body = lines[index + 1 : fence.body_end]
            _take_script(current, fence, body, windows=windows)
            fence_list = reader.fence_list_indent(line)
        if index in code:
            reader.reset(list_indent=fence_list)
            continue
        if (heading := reader.read(current, line)) is None:
            continue
        level, text = heading
        if level > 1:
            commands.append(current)
        elif commands:
            break
        current = _Command(level=level, name=_command_name(text, labels))
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


def _runnable(accepted: list[_Command], run: list[_Command]) -> list[_Command]:
    """Resolve repeated sibling names the way mask's CLI does.

    mask's argument parser accepts a path through the *first* command of a
    repeated name, but then runs the *last* one, looking up the rest of the
    path among that command's subcommands. So a repeated command runs its last
    definition, and a subcommand under it is runnable as itself only when both
    definitions have one of that name; nor does the bare name run when the
    first definition is a group without a script. Commands that only render alike
    (``## deploy prod`` vs ``prod`` under ``## deploy``) are distinct and stay.
    """
    first: dict[str, _Command] = {}
    for command in accepted:
        first.setdefault(command.name, command)
    last = {command.name: command for command in run}
    resolved: list[_Command] = []
    for name, command in last.items():
        if name in first:
            if first[name].script is None and first[name].subcommands:
                # The parser demands a subcommand after a command it knows
                # only as a group, so the bare name cannot run.
                command.script = None
            command.subcommands = _runnable(
                first[name].subcommands, command.subcommands
            )
            resolved.append(command)
    return resolved


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
                    # Named as the shell words after `mask`, so `deploy prod`
                    # (a subcommand) and `'deploy prod'` (one command whose
                    # name has a space) stay distinct.
                    name=shlex.join(path),
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
    return _tasks(_runnable(root.subcommands, root.subcommands), (), source_file)


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
