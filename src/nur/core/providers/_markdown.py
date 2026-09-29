"""Line-level markdown helpers shared by the markdown-driven providers.

Both xc (``README.md``) and mask (``maskfile.md``) read task structure out of
headings and fenced code blocks, so they share how those are recognised.
"""

from __future__ import annotations

import re

__all__ = ["FENCE", "HEADING", "code_lines", "fence_blocks"]


# Markdown allows up to three leading spaces before a heading or fence; a fourth
# makes the line an indented code block instead. Without that bound, a file that
# shows indented examples would advertise phantom tasks.
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$")
# The trailing group is a fence's info string. Only an opening fence may carry
# one: a closing fence must have nothing but whitespace after its delimiter.
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


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
            if match is not None:
                opener, open_index = match.group(1), index
            continue
        if match is None:
            continue
        delimiter, info = match.groups()
        closes = (
            delimiter[0] == opener[0]
            and len(delimiter) >= len(opener)
            and not info.strip()
        )
        if closes:
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
