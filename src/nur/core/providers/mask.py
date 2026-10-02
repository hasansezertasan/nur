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
    DEFINITION_TITLE,
    heading_text,
    normalize_label,
    valid_definition_target,
    valid_reference_label,
)
from nur.core.providers._markdown import (
    BLANK_LINE,
    HEADING,
    SETEXT_UNDERLINE,
    THEMATIC_BREAK,
    Fence,
    container_content,
    container_line,
    indent_width,
    leaves_paragraph_open,
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
BLOCKQUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
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
    # Each block overwrites the last: mask runs the final one.
    # mask refuses to run a script without a language tag (the tag selects the
    # interpreter) or without any line; a script of blank lines still runs.
    runnable = bool(info) and bool(body)
    # Each body line loses up to the fence's own indentation, by columns.
    script = "\n".join(
        strip_columns(container_line(fence.containers, line) or "", fence.indent)
        for line in body
    )
    if None in fence.containers:
        command.quote = [script]
    if not windows and info in WINDOWS_ONLY_EXECUTORS:
        return
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
    lists: list[int] = field(default_factory=list)

    @property
    def list_indent(self) -> int | None:
        """The content column of the innermost open list item."""
        return self.lists[-1] if self.lists else None

    def reset(self, *, list_indent: int | None) -> None:
        self.quote, self.paragraph, self.boundary = [], [], True
        if list_indent is None:
            self.lists.clear()
        else:
            while self.lists and self.lists[-1] > list_indent:
                self.lists.pop()
            if not self.lists or self.lists[-1] != list_indent:
                self.lists.append(list_indent)

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
        # A dedent can return to an outer item; keep every content column.
        if BLANK_LINE.fullmatch(line) is None:
            width = indent_width(line)
            while self.lists and width < self.lists[-1]:
                self.lists.pop()
                self.paragraph = []
        # Inside a list item, block structure is read relative to its content.
        inside = self._in_item(line) and (BLANK_LINE.fullmatch(line) is None)
        inner = strip_columns(line, self.list_indent or 0) if inside else line
        if (
            self.boundary
            and BLANK_LINE.fullmatch(inner) is None
            and indent_width(inner) >= INDENTED_CODE
        ):
            # An indented code block. mask runs its last code block, and this
            # one has no language tag, so mask cannot run this command.
            command.script = None
            return None
        if (match := BLOCKQUOTE.match(inner)) is not None:
            return self._read_quote(command, match.group(1))
        heading = HEADING.match(inner)
        if heading is None and self._continues_quote(inner):
            self.quote.append(inner.strip(" \t\r\n\f\v"))
            return None
        self.quote = []
        if heading is not None:
            return self._heading(heading)
        after_boundary = self.boundary
        self.boundary = BLANK_LINE.fullmatch(line) is not None
        offset = (self.list_indent or 0) if inside else 0
        return self._read_text(line, inner, offset, after_boundary=after_boundary)

    def _read_quote(self, command: _Command, content: str) -> tuple[int, str] | None:
        content, _ = container_content(content)
        if not self.quote:
            # The last blockquote under a heading is its description.
            command.quote = self.quote
        if (self.boundary or not self.quote or not self.quote[-1]) and indent_width(
            content
        ) >= INDENTED_CODE:
            command.script = None
            self.quote.append(content.strip(" \t\r\n\f\v"))
            self.paragraph, self.boundary = [], True
            return None
        if (heading := HEADING.match(content)) is not None:
            return self._heading(heading)
        if (
            self.quote
            and not self.boundary
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
        self.quote.append(content.strip(" \t\r\n\f\v"))
        self.paragraph, self.boundary = [], BLANK_LINE.fullmatch(content) is not None
        return None

    def _read_list_item(self, indent: int, content: str) -> tuple[int, str] | None:
        while self.lists and self.lists[-1] >= indent:
            self.lists.pop()
        self.lists.append(indent)
        content, containers = container_content(content)
        # Nested marker lines can open several list levels at once. Columns
        # after a quote belong to that quote, not the outer list's indentation.
        for width in containers:
            if width is None:
                break
            indent += width
            self.lists.append(indent)
        # A heading may sit on the item's own line: `- ## build`. Otherwise
        # its text opens a paragraph, which an underline can make a heading.
        if (heading := HEADING.match(content)) is not None:
            return self._heading(heading)
        self.paragraph = (
            [content.strip(" \t\r\n\f\v")]
            if BLANK_LINE.fullmatch(content) is None
            else []
        )
        return None

    def _heading(self, heading: re.Match[str]) -> tuple[int, str]:
        self.quote, self.paragraph, self.boundary = [], [], True
        return len(heading.group(1)), heading.group(2) or ""

    def _continues_quote(self, line: str) -> bool:
        """Whether *line* lazily continues the blockquote's paragraph."""
        return (
            bool(self.quote and self.quote[-1])
            and BLANK_LINE.fullmatch(line) is None
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
        if BLANK_LINE.fullmatch(line) is not None:
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
        if thematic:
            # A thematic break outside an item ends the list. Valid reference
            # definitions have already been consumed by the definition pass.
            self.paragraph, self.boundary = [], True
            if not offset:
                self.lists.clear()
            return None
        if (item := _list_item(inner, paragraph=bool(self.paragraph))) is not None:
            return self._read_list_item(offset + item[0], item[1])
        if self.list_indent is not None and (
            not after_boundary or indent_width(line) >= self.list_indent
        ):
            # The list item continues: its paragraph, or a new one after a gap.
            if after_boundary:
                self.paragraph = []
            self.paragraph.append(line.strip(" \t\r\n\f\v"))
            return None
        self.lists.clear()
        self.paragraph.append(line.strip(" \t\r\n\f\v"))
        return None


def _following_definition_title(
    lines: list[str], index: int, containers: tuple[int | None, ...], target: str
) -> bool:
    """Whether the definition's optional title is on *lines[index]*."""
    match = DEFINITION_TARGET.match(target)
    if index >= len(lines) or match is None or target.strip(" \t") != match.group(1):
        return False
    content = container_line(containers, lines[index])
    return content is not None and DEFINITION_TITLE.match(content) is not None


def _definition_lines(
    lines: list[str],
    span: range,
    containers: tuple[int | None, ...],
    code: set[int],
    target: str,
) -> set[int]:
    """Return the definition marker, destination, and any following title line."""
    end = span.stop
    if end not in code and _following_definition_title(lines, end, containers, target):
        end += 1
    return set(range(span.start, end))


def _definitions(lines: list[str], code: set[int]) -> tuple[frozenset[str], set[int]]:
    """Collect the labels of the link reference definitions outside code.

    Definitions may follow the headings that use them. Each needs exactly a
    destination and an optional title, after its colon or on the next line:
    `[build]:` alone, or with trailing text, is not a definition.
    """
    labels: set[str] = set()
    consumed: set[int] = set()
    containers: tuple[int | None, ...] = ()
    paragraph = False
    for index, line in enumerate(lines):
        if index in code:
            paragraph = False
            continue
        # Reuse the block scanner's container rules, including nested markers
        # and list continuation indentation. Code and HTML remain excluded.
        inner = container_line(containers, line)
        if inner is None:
            containers = ()
            paragraph = False
            inner = line
        content, opened = container_content(inner)
        if opened:
            paragraph = False
        containers += opened
        if index in consumed:
            continue
        match = None if paragraph else DEFINITION.match(content)
        if match is None or not valid_reference_label(match.group(1)):
            paragraph = leaves_paragraph_open(content, paragraph=paragraph)
            continue
        target = content[match.end() :]
        next_line = (
            not target.strip(" \t") and index + 1 < len(lines) and index + 1 not in code
        )
        if next_line:
            target = container_line(containers, lines[index + 1]) or ""
        if valid_definition_target(target):
            labels.add(normalize_label(match.group(1)))
            consumed.update(
                _definition_lines(
                    lines,
                    range(index, index + (2 if next_line else 1)),
                    containers,
                    code,
                    target,
                )
            )
        else:
            paragraph = True
    return frozenset(labels), consumed


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
    labels, definitions = _definitions(lines, code)

    commands: list[_Command] = []
    current = _Command(level=1)
    reader = _Reader()
    fence_list: int | None = None
    for index, line in enumerate(lines):
        if index in openings:
            fence = openings[index]
            _take_script(
                current, fence, lines[index + 1 : fence.body_end], windows=windows
            )
            fence_list = reader.fence_list_indent(line)
        if index in definitions:
            reader.reset(list_indent=reader.list_indent)
            continue
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
            text = (cwd / SOURCE_FILE).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping %s (%s)", SOURCE_FILE, exc)
            return []
        return parse_mask(text, SOURCE_FILE)
