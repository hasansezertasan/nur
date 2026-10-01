"""Line-level markdown helpers shared by the markdown-driven providers.

Both xc (``README.md``) and mask (``maskfile.md``) read task structure out of
headings and fenced code blocks, so they share how those are recognised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from typing import TypeIs

__all__ = [
    "FENCE",
    "HEADING",
    "LIST_ITEM_FENCE",
    "SETEXT_UNDERLINE",
    "THEMATIC_BREAK",
    "Fence",
    "code_lines",
    "fence_blocks",
    "indent_width",
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
# Past four spaces after the marker, the line is an indented code block instead.
LIST_ITEM_FENCE = re.compile(
    r"^ {0,3}(?:(?:[-*+]|\d{1,9}[.)])[ \t]{1,4})?(`{3,}|~{3,})(.*)$"
)
# The markers of a blockquote, or of a list item, that may precede an HTML block.
QUOTE_PREFIX = re.compile(r"^(?: {0,3}> ?)+")
LIST_MARKER = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]{1,4}(?=\S)|[ \t])")

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
    # A raw-text block ends only at its own closing tag.
    *(
        (
            re.compile(rf"^ {{0,3}}<{tag}(?:\s|>|$)", re.IGNORECASE),
            re.compile(rf"</{tag}>", re.IGNORECASE),
        )
        for tag in ("pre", "script", "style", "textarea")
    ),
    # Markdown, unlike an HTML parser, ends a comment block only at `-->`:
    # mask still hides a heading after `--!>`, so that must not end it here.
    # This reads markdown structure; it is not an HTML sanitizer.
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
THEMATIC_BREAK = re.compile(
    r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
# A setext heading's underline: `===` for level 1, `---` for level 2.
SETEXT_UNDERLINE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
EMPTY_QUOTE = re.compile(r"^ {0,3}>\s*$")
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


def indent_width(line: str) -> int:
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
        if line.strip() and indent_width(line) < fence.indent:
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


def _html_block_at(line: str, *, paragraph: bool) -> _HtmlBlock | None:
    """Return the HTML block opening on *line*, inside any container, if any."""
    content, quoted, indent = _container_content(line)
    end = _html_block_end(content, paragraph=paragraph)
    return _HtmlBlock(end, quoted, indent) if end is not None else None


def _leaves_paragraph_open(line: str, *, paragraph: bool) -> bool:
    """Whether a paragraph is still open after *line*.

    A lone tag on the next line then continues the paragraph instead of
    starting an HTML block.
    """
    if not line.strip() or HEADING.match(line) or EMPTY_QUOTE.match(line):
        return False
    if THEMATIC_BREAK.match(line):
        return False
    # An underline under a paragraph turns it into a setext heading.
    return not (paragraph and SETEXT_UNDERLINE.match(line))


@dataclass
class _HtmlBlock:
    end: re.Pattern[str]
    # The container the block opened in: a blockquote, or a list item's
    # content column. The block ends with its container.
    quoted: bool
    indent: int | None


def _container_content(line: str) -> tuple[str, bool, int | None]:
    """Split a blockquote's or list item's markers off *line*."""
    if (match := QUOTE_PREFIX.match(line)) is not None:
        return line[match.end() :], True, None
    if not THEMATIC_BREAK.match(line) and (match := LIST_MARKER.match(line)):
        return line[match.end() :], False, match.end()
    return line, False, None


def _html_continues(block: _HtmlBlock, line: str) -> bool | None:
    """Whether *line* belongs to the open HTML *block*.

    Returns None when the block's container ends first, so the line is read
    afresh; False when a blank line ends the block (the line is not part of
    it); True when the line is hidden inside the block.
    """
    if block.quoted and QUOTE_PREFIX.match(line) is None:
        return None
    if block.indent is not None and line.strip() and indent_width(line) < block.indent:
        return None
    content = QUOTE_PREFIX.sub("", line, count=1) if block.quoted else line
    return not (block.end is BLANK_LINE and not content.strip())


def _still_open(end: re.Pattern[str], line: str) -> re.Pattern[str] | None:
    """Return *end* if an HTML block stays open after its *line*, else None."""
    if end is not BLANK_LINE and end.search(line) is not None:
        return None
    return end


@dataclass
class _Scanner:
    """The fence or HTML block open while :func:`scan` walks the lines."""

    list_items: bool
    html: bool
    fences: list[Fence] = field(default_factory=list)
    hidden: set[int] = field(default_factory=set)
    fence: _Open | None = None
    block: _HtmlBlock | None = None
    paragraph: bool = False

    def read(self, index: int, line: str) -> None:
        if self.fence is not None and self._in_fence(self.fence, index, line):
            return
        if self.block is not None and self._in_html(self.block, index, line):
            return
        opening = LIST_ITEM_FENCE if self.list_items else FENCE
        if _opens(match := opening.match(line)):
            indent = match.start(1)
            in_item = indent > _leading_spaces(line)
            self.fence = _Open(index, match.group(1), indent, in_item)
            self.paragraph = False
        elif self.html and (block := _html_block_at(line, paragraph=self.paragraph)):
            self.hidden.add(index)
            # The whole opening line counts: `<!-->` closes where it opens.
            self.block = block if _still_open(block.end, line) else None
            self.paragraph = False
        else:
            self.paragraph = _leaves_paragraph_open(line, paragraph=self.paragraph)

    def _in_fence(self, fence: _Open, index: int, line: str) -> bool:
        """Consume *line* if it belongs to the open *fence*, closing it if done."""
        if (block := _fence_ends(fence, line, index)) is None:
            return True
        self.fences.append(block)
        self.fence, self.paragraph = None, False
        # A closing fence line is the fence's; otherwise the item ended and
        # the line is read afresh.
        return block.end > index

    def _in_html(self, block: _HtmlBlock, index: int, line: str) -> bool:
        """Consume *line* if it belongs to the open HTML *block*."""
        inside = _html_continues(block, line)
        if inside is None:
            self.block = None  # its container ended; read the line afresh
            return False
        if inside:
            self.hidden.add(index)
            if _still_open(block.end, line) is None:
                self.block = None
        else:
            self.block, self.paragraph = None, False
        return True


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
    scanner = _Scanner(list_items, html)
    for index, line in enumerate(lines):
        scanner.read(index, line)
    if scanner.fence is not None:
        start = scanner.fence.index
        scanner.fences.append(Fence(start, len(lines), len(lines)))
    return scanner.fences, scanner.hidden


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
