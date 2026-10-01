"""Inline markdown in a mask heading, reduced to the text mask's parser keeps.

mask (pulldown-cmark 0.5) builds a heading's text from its inline events and
starts over at every opening tag: emphasis, strong emphasis, and links. So
the text that names a command is what follows the heading's last such opener,
with markup removed -- ``## a *b* c`` is ``b c``. Code spans are not tags;
mask writes them back between single backticks. Escapes and semicolon-ended
character references are decoded.
"""

from __future__ import annotations

import bisect
import html
import re
import unicodedata
from dataclasses import dataclass

__all__ = ["heading_text"]


ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
REFERENCE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
# A link or image whose destination may hold escaped parentheses or one level
# of balanced ones. No bracket may repeat inside, which keeps matching linear.
LINK = re.compile(r"!?\[([^\[\]]*)\]\((?:[^()\\]|\\.|\([^()]*\))*\)")
BACKTICKS = re.compile(r"`+")
# Delimiters a strong emphasis consumes from each side; plain emphasis takes one.
STRONG = 2


@dataclass
class _Delimiter:
    """A run of `*` or `_` that may open or close emphasis."""

    char: str
    count: int
    can_open: bool
    can_close: bool
    opens: bool = False


# A token is literal text, a link's opening tag (None), or a delimiter run.
_Token = str | _Delimiter | None


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith(("P", "S"))


def _delimiter(text: str, start: int, end: int) -> _Delimiter:
    """Classify the delimiter run ``text[start:end]`` by its flanking."""
    before = text[start - 1] if start else " "
    after = text[end] if end < len(text) else " "
    left = not after.isspace() and (
        not _is_punctuation(after) or before.isspace() or _is_punctuation(before)
    )
    right = not before.isspace() and (
        not _is_punctuation(before) or after.isspace() or _is_punctuation(after)
    )
    if text[start] == "*":
        return _Delimiter("*", end - start, can_open=left, can_close=right)
    return _Delimiter(
        "_",
        end - start,
        can_open=left and (not right or _is_punctuation(before)),
        can_close=right and (not left or _is_punctuation(after)),
    )


class _Tokenizer:
    def __init__(self, text: str) -> None:
        self.text = text
        # Backtick runs by length, so a code span finds its closer by bisection
        # instead of rescanning the rest of the heading.
        self.runs: dict[int, list[int]] = {}
        for run in BACKTICKS.finditer(text):
            self.runs.setdefault(len(run.group()), []).append(run.start())

    def tokens(self, start: int, stop: int) -> list[_Token]:
        text, out, index = self.text, [], start
        while index < stop:
            char = text[index]
            if (escaped := ESCAPED.match(text, index, stop)) is not None:
                out.append(escaped.group(1))
                index = escaped.end()
            elif (reference := REFERENCE.match(text, index, stop)) is not None:
                out.append(html.unescape(reference.group()))
                index = reference.end()
            elif char == "`":
                index = self._code(index, stop, out)
            elif char in "[!" and (link := LINK.match(text, index, stop)) is not None:
                out.append(None)
                out.extend(self.tokens(link.start(1), link.end(1)))
                index = link.end()
            elif char in "*_":
                end = index
                while end < stop and text[end] == char:
                    end += 1
                out.append(_delimiter(text, index, end))
                index = end
            else:
                out.append(char)
                index += 1
        return out

    def _code(self, index: int, stop: int, out: list[_Token]) -> int:
        """Append the code span opening at *index*, or its backticks as text."""
        run = BACKTICKS.match(self.text, index)
        length = len(run.group()) if run is not None else 1
        runs = self.runs[length]
        position = bisect.bisect_right(runs, index)
        close = runs[position] if position < len(runs) else None
        if close is None or close + length > stop:
            out.append("`" * length)
            return index + length
        content = self.text[index + length : close]
        if content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]
        out.append(f"`{content}`")
        return close + length


def _match_emphasis(tokens: list[_Token]) -> None:
    """Pair openers with closers, marking each opener that starts emphasis."""
    openers: dict[str, list[_Delimiter]] = {"*": [], "_": []}
    for token in tokens:
        if not isinstance(token, _Delimiter):
            continue
        stack = openers[token.char]
        while token.can_close and token.count and stack:
            opener = stack[-1]
            strong = opener.count >= STRONG and token.count >= STRONG
            used = STRONG if strong else 1
            opener.count -= used
            token.count -= used
            opener.opens = True
            if not opener.count:
                stack.pop()
        if token.can_open and token.count:
            stack.append(token)


def heading_text(text: str) -> str:
    """Return the text mask names a heading by, from its raw inline markdown."""
    tokens = _Tokenizer(text).tokens(0, len(text))
    _match_emphasis(tokens)
    starts = [
        index
        for index, token in enumerate(tokens)
        if token is None or (isinstance(token, _Delimiter) and token.opens)
    ]
    parts: list[str] = []
    for token in tokens[starts[-1] + 1 if starts else 0 :]:
        if isinstance(token, _Delimiter):
            parts.append(token.char * token.count)  # unmatched, so literal
        elif token is not None:
            parts.append(token)
    return "".join(parts)
