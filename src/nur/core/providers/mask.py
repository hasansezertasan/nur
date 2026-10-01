from __future__ import annotations

import html
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._markdown import (
    HEADING,
    LIST_ITEM_FENCE,
    SETEXT_UNDERLINE,
    THEMATIC_BREAK,
    fence_column,
    indent_width,
    list_item_content,
    scan,
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
# List items that end a blockquote's paragraph instead of lazily continuing it.
PARAGRAPH_INTERRUPT = re.compile(r"^ {0,3}(?:[-*]|1[.)])(?:[ \t]|$)")
# An inline link or image, whose destination may hold escaped or one level of
# balanced parentheses. No bracket may repeat inside, which keeps a scan from
# every `[` of a hostile heading linear.
LINK = re.compile(r"!?\[([^\[\]]*)\]\((?:[^()\\]|\\.|\([^()]*\))*\)")
# A backslash escape of ASCII punctuation, which markdown reads as the literal,
# or a character reference, which markdown decodes only with its semicolon.
# One pass, so an escaped `\&` stays literal rather than starting a reference.
ESCAPE = re.compile(
    r"\\([!-/:-@\[-`{-~])|(&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});)"
)
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


def _escaped(text: str, link: re.Match[str]) -> bool:
    """Whether the link's `[` is escaped by an odd run of backslashes."""
    start = bracket = link.start() + (text[link.start()] == "!")
    while start and text[start - 1] == "\\":
        start -= 1
    return (bracket - start) % 2 == 1


def _unescape(match: re.Match[str]) -> str:
    escaped, reference = match.groups()
    return escaped if escaped is not None else html.unescape(reference)


def _command_name(text: str) -> str:
    """Strip a heading's ``(required)`` and ``[optional]`` argument declarations."""
    text = text.strip()
    # Drop an ATX heading's optional closing sequence: hashes that fill the
    # heading or follow whitespace. Done without a regex, which backtracks
    # quadratically over a long whitespace run in a hostile heading.
    head = text.rstrip("#")
    if head != text and (not head or head[-1].isspace()):
        text = head.rstrip()
    # mask restarts a heading's text at each link or image, so the last one
    # names the command from its own text onward: `## x [a](u) y` is `a y`.
    if links := [link for link in LINK.finditer(text) if not _escaped(text, link)]:
        text = links[-1].group(1) + text[links[-1].end() :]
    text = ESCAPE.sub(_unescape, text)
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
    indent = (fence_column(fence, match) or 0) if match is not None else 0
    command.script = _dedent(body, indent) if runnable else None


def _list_item(line: str) -> tuple[int, str] | None:
    """Return a list item's content column and content, if *line* opens one."""
    if (item := list_item_content(line)) is None:
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

    def read(self, command: _Command, line: str) -> tuple[int, str] | None:
        """Read one line under *command*; return a heading's (level, text)."""
        base = self.list_indent or 0
        if self.boundary and line.strip() and indent_width(line) >= base + 4:
            # An indented code block. mask runs its last code block, and this
            # one has no language tag, so mask cannot run this command.
            command.script = None
            return None
        if (match := BLOCKQUOTE.match(line)) is not None:
            return self._read_quote(command, match.group(1))
        heading = HEADING.match(line)
        if heading is None and self._continues_quote(line):
            self.quote.append(line.strip())
            return None
        self.quote = []
        if heading is not None:
            return self._heading(heading)
        after_boundary = self.boundary
        self.boundary = not line.strip()
        return self._read_text(line, after_boundary=after_boundary)

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
        self.paragraph, self.list_indent = [], indent
        # A heading may sit on the item's own line: `- ## build`.
        heading = HEADING.match(content)
        return self._heading(heading) if heading is not None else None

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

    def _read_text(self, line: str, *, after_boundary: bool) -> tuple[int, str] | None:
        if not line.strip():
            self.paragraph = []
            return None
        if self.paragraph and (underline := SETEXT_UNDERLINE.match(line)):
            text = " ".join(self.paragraph)
            self.paragraph, self.boundary = [], True
            return (1 if underline.group(1)[0] == "=" else 2), text
        if THEMATIC_BREAK.match(line):
            self.paragraph, self.boundary, self.list_indent = [], True, None
            return None
        if (item := _list_item(line)) is not None:
            return self._read_list_item(*item)
        if self.list_indent is not None and (
            not after_boundary or indent_width(line) >= self.list_indent
        ):
            return None  # the list item continues
        self.list_indent = None
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
    fence_list: int | None = None
    for index, line in enumerate(lines):
        if index in openings:
            _take_script(
                current, line, lines[index + 1 : openings[index]], windows=windows
            )
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
