"""Line-level markdown helpers shared by the markdown-driven providers.

Both xc (``README.md``) and mask (``maskfile.md``) read task structure out of
headings and fenced code blocks, so they share how those are recognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import TypeIs

__all__ = [
    "FENCE",
    "HEADING",
    "LIST_ITEM_FENCE",
    "Fence",
    "code_lines",
    "fence_blocks",
    "scan",
]


# Markdown allows up to three leading spaces before a heading, fence, or HTML
# block; a fourth makes the line an indented code block instead. Without that
# bound, a file that shows indented examples would advertise phantom tasks.
# Only a space or tab may separate the hashes from the text. CommonMark reads a
# bare run of hashes as an empty heading, so the text group is optional; a
# provider whose runner disagrees (xc) checks for the missing group itself.
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*))?$")
# The trailing group is a fence's info string. Only an opening fence may carry
# one: a closing fence must have nothing but whitespace after its delimiter.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A fence may also open on a list item's marker line, e.g. ``- ```sh``.
LIST_ITEM_FENCE = re.compile(
    r"^ {0,3}(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?(`{3,}|~{3,})(.*)$"
)

# HTML block start conditions (CommonMark 4.6), each paired with the pattern
# that ends the block, or BLANK_LINE for a block that runs to a blank line.
BLANK_LINE = re.compile(r"^\s*$")
_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|"
    "colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|"
    "form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|"
    "menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|"
    "summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
HTML_BLOCKS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(r"^ {0,3}<(?:pre|script|style|textarea)(?:\s|>|$)", re.IGNORECASE),
        re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE),
    ),
    (re.compile(r"^ {0,3}<!--"), re.compile(r"-->")),
    (re.compile(r"^ {0,3}<\?"), re.compile(r"\?>")),
    (re.compile(r"^ {0,3}<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"^ {0,3}<!\[CDATA\["), re.compile(r"\]\]>")),
    (
        re.compile(rf"^ {{0,3}}</?(?:{_BLOCK_TAGS})(?:\s|/?>|$)", re.IGNORECASE),
        BLANK_LINE,
    ),
)
_ATTRIBUTE = r"""\s+[A-Za-z_:][\w.:-]*(?:\s*=\s*(?:[^\s"'=<>`]+|'[^']*'|"[^"]*"))?"""
# Any other complete opening or closing tag alone on its line (`<img ...>`).
HTML_LONE_TAG = re.compile(
    rf"^ {{0,3}}(?:<[A-Za-z][A-Za-z0-9-]*(?:{_ATTRIBUTE})*\s*/?>"
    r"|</[A-Za-z][A-Za-z0-9-]*\s*>)\s*$"
)


def _opens(match: re.Match[str] | None) -> TypeIs[re.Match[str]]:
    # A backtick fence's info string may not itself contain a backtick:
    # ```` ``` sh ``` ```` is an inline code span, not an opening fence.
    return match is not None and not (
        match.group(1)[0] == "`" and "`" in match.group(2)
    )


def _closes(match: re.Match[str] | None, opener: str) -> bool:
    if match is None:
        return False
    delimiter, info = match.groups()
    return (
        delimiter[0] == opener[0] and len(delimiter) >= len(opener) and not info.strip()
    )


class Fence(NamedTuple):
    """A fenced code block: its opening line and where its body and block end.

    Both ends are exclusive indices. ``end`` is one past the closing fence line
    when there is one; a fence left open by the end of its list item or of the
    input ends where its body does.
    """

    open: int
    body_end: int
    end: int


@dataclass
class _Open:
    index: int
    delimiter: str
    # The column the fence's content starts at, and whether the fence sits on a
    # list item's marker line (so the item's end also ends the fence).
    indent: int
    in_item: bool


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _indent_width(line: str) -> int:
    """Return *line*'s indentation in columns, a tab advancing to a multiple of 4."""
    width = 0
    for char in line:
        if char == " ":
            width += 1
        elif char == "\t":
            width += 4 - width % 4
        else:
            break
    return width


def _fence_ends(fence: _Open, line: str, index: int) -> Fence | None:
    """Return the finished block if *line* ends the open *fence*."""
    if fence.in_item:
        if line.strip() and _indent_width(line) < fence.indent:
            # The list item ends here, and a fence inside it ends with it.
            return Fence(fence.index, index, index)
        line = line[min(fence.indent, _leading_spaces(line)) :]
    if _closes(FENCE.match(line), fence.delimiter):
        return Fence(fence.index, index, index + 1)
    return None


def _html_block_end(line: str, *, paragraph: bool) -> re.Pattern[str] | None:
    """Return the end condition of an HTML block opening on *line*, if any.

    The CommonMark start conditions, most specific first; ``BLANK_LINE`` means
    the block runs to the next blank line. A lone complete tag (the last kind)
    cannot interrupt a *paragraph*.
    """
    for start, end in HTML_BLOCKS:
        if start.match(line) is not None:
            return end
    if not paragraph and HTML_LONE_TAG.match(line) is not None:
        return BLANK_LINE
    return None


def _still_open(end: re.Pattern[str], line: str) -> re.Pattern[str] | None:
    """Return *end* if an HTML block stays open after its *line*, else None."""
    if end is not BLANK_LINE and end.search(line) is not None:
        return None
    return end


def scan(
    lines: list[str], *, list_items: bool = False, html: bool = False
) -> tuple[list[Fence], set[int]]:
    """Locate fenced code blocks and, with *html*, the lines of HTML blocks.

    A fence closes on the next line opening with the same character repeated at
    least as many times and followed by nothing but whitespace. That is what
    lets a ````-fenced block contain ``` lines (as xc's own documentation does),
    and what keeps a script line such as ```not-a-close`` as script content
    rather than a delimiter. An unclosed fence runs to the end of the input.

    With *list_items*, a fence may also open on a list item's marker line
    (``- ```sh``), as in CommonMark, and then ends with the item; otherwise its
    indented closer would be misread as a new opening fence.

    With *html*, raw HTML blocks are collected too, since markdown does not read
    a heading or fence inside one -- a commented-out command, or one wrapped in
    ``<details>``, is not structure. A comment runs through the line containing
    ``-->``, a ``<div>``-style block to the next blank line. HTML inside a fence
    is script content, and a fence inside HTML is not a fence.
    """
    opening = LIST_ITEM_FENCE if list_items else FENCE
    fences: list[Fence] = []
    hidden: set[int] = set()
    fence: _Open | None = None
    end: re.Pattern[str] | None = None
    paragraph = False
    for index, line in enumerate(lines):
        if fence is not None:
            if (block := _fence_ends(fence, line, index)) is None:
                continue
            fences.append(block)
            fence = None
            paragraph = False
            if block.end > index:
                continue  # a closing fence line; otherwise read the line anew
        if end is not None:
            if end is BLANK_LINE and not line.strip():
                end, paragraph = None, False
            else:
                hidden.add(index)
                end = _still_open(end, line)
            continue
        if _opens(match := opening.match(line)):
            indent = match.start(1)
            fence = _Open(index, match.group(1), indent, indent > _leading_spaces(line))
            paragraph = False
            continue
        if html and (end := _html_block_end(line, paragraph=paragraph)) is not None:
            hidden.add(index)
            # The whole opening line counts: `<!-->` closes where it opens.
            end, paragraph = _still_open(end, line), False
            continue
        paragraph = bool(line.strip()) and HEADING.match(line) is None
    if fence is not None:
        fences.append(Fence(fence.index, len(lines), len(lines)))
    return fences, hidden


def fence_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Locate fenced code blocks as ``(opening line, closing line)`` indices.

    An unclosed fence's closing index is one past the last line. See
    :func:`scan` for how fences open and close.
    """
    return [(block.open, block.body_end) for block in scan(lines)[0]]


def code_lines(lines: list[str], blocks: list[tuple[int, int]]) -> set[int]:
    """Collect the line indices belonging to a fenced block, fences included.

    Headings and other structure are only meaningful outside these lines: a
    shell script full of ``# comment`` lines, or a file that quotes task syntax
    in an example block, must not be mistaken for structure.
    """
    code: set[int] = set()
    for open_index, close_index in blocks:
        code.update(range(open_index, min(close_index + 1, len(lines))))
    return code
