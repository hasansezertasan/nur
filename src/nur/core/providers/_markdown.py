"""Line-level markdown helpers shared by the markdown-driven providers.

Both xc (``README.md``) and mask (``maskfile.md``) read task structure out of
headings and fenced code blocks, so they share how those are recognised.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import TypeIs

__all__ = ["FENCE", "HEADING", "code_lines", "comment_lines", "fence_blocks"]


# Markdown allows up to three leading spaces before a heading or fence; a fourth
# makes the line an indented code block instead. Without that bound, a file that
# shows indented examples would advertise phantom tasks.
# A bare run of hashes is an empty heading, so the text group is optional.
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:\s+(.*))?$")
# The trailing group is a fence's info string. Only an opening fence may carry
# one: a closing fence must have nothing but whitespace after its delimiter.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# An HTML comment starting a line hides everything up to the line closing it.
COMMENT_OPEN = re.compile(r"^ {0,3}<!--")


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


def fence_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """Locate fenced code blocks as ``(opening line, closing line)`` indices.

    A fence closes on the next line opening with the same character repeated at
    least as many times and followed by nothing but whitespace. That is what
    lets a ````-fenced block contain ``` lines (as xc's own documentation does),
    and what keeps a script line such as ```not-a-close`` as script content
    rather than a delimiter. An unclosed fence runs to the end of the input, so
    its closing index is one past the last line.
    """
    blocks: list[tuple[int, int]] = []
    opener: str | None = None
    open_index = 0
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if opener is None:
            if _opens(match):
                opener, open_index = match.group(1), index
        elif _closes(match, opener):
            blocks.append((open_index, index))
            opener = None
    if opener is not None:
        blocks.append((open_index, len(lines)))
    return blocks


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


def comment_lines(lines: list[str]) -> set[int]:
    """Collect the line indices of HTML comment blocks outside fenced code.

    A comment block starts on a line opening with ``<!--`` and runs through the
    line containing ``-->`` (or to the end of the input), hiding any heading or
    fence inside it -- a command that has been commented out is not structure.
    A ``<!--`` inside a fenced block is script content, not a comment.
    """
    comments: set[int] = set()
    opener: str | None = None
    in_comment = False
    for index, line in enumerate(lines):
        if in_comment:
            comments.add(index)
            in_comment = "-->" not in line
            continue
        match = FENCE.match(line)
        if opener is not None:
            if _closes(match, opener):
                opener = None
        elif _opens(match):
            opener = match.group(1)
        elif COMMENT_OPEN.match(line) is not None:
            comments.add(index)
            in_comment = "-->" not in line.split("<!--", 1)[1]
    return comments
