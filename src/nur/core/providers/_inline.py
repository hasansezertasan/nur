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
import operator
import re
import unicodedata
from dataclasses import dataclass

__all__ = ["DEFINITION", "DEFINITION_TARGET", "heading_text", "normalize_label"]


ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
REFERENCE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
# A link or image. Its destination is `<...>`, or bare: no whitespace, with
# escaped or one level of balanced parentheses. A quoted or parenthesized
# title may follow. No bracket may repeat inside, which keeps matching linear.
_DESTINATION = r"(?:<[^<>\n]*>|(?:[^()\s\\]|\\.|\([^()\s]*\))*)"
_TITLE = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))"""
_LINK_CHAR = r"(?:[^\[\]\\]|\\.)"
_LINK_TEXT = rf"{_LINK_CHAR}*"
LINK = re.compile(rf"!?\[({_LINK_TEXT})\]\(\s*{_DESTINATION}(?:\s+{_TITLE})?\s*\)")
# A full or collapsed reference link, `[text][label]` or `[text][]`, and a
# shortcut one, `[text]`; each is a link only when the document defines it.
FULL_REFERENCE = re.compile(rf"!?\[({_LINK_TEXT})\]\[({_LINK_TEXT})\]")
SHORTCUT_REFERENCE = re.compile(rf"!?\[({_LINK_CHAR}+)\](?![(\[])")
# A link reference definition line, `[label]: destination`, and what may follow
# its colon (here or on the next line): a destination and an optional title.
DEFINITION = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.)+)\]:(?:[ \t]|$)")
DEFINITION_TARGET = re.compile(
    r"""^[ \t]*(<[^<>\n]*>|[^\s<]\S*)(?:[ \t]+"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?[ \t]*$"""
)
BACKTICKS = re.compile(r"`+")
# Delimiters a strong emphasis consumes from each side; plain emphasis takes one.
STRONG = 2


@dataclass
class _Delimiter:
    """A run of `*` or `_` that may open or close emphasis."""

    char: str
    count: int
    length: int
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
        return _Delimiter("*", end - start, end - start, can_open=left, can_close=right)
    return _Delimiter(
        "_",
        end - start,
        end - start,
        can_open=left and (not right or _is_punctuation(before)),
        can_close=right and (not left or _is_punctuation(after)),
    )


def normalize_label(label: str) -> str:
    """Return the form two link labels must share to match: case and spacing fold."""
    return " ".join(label.split()).casefold()


class _Tokenizer:
    def __init__(self, text: str, labels: frozenset[str]) -> None:
        self.text = text
        self.labels = labels
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
            elif char in "[!" and (link := self._link(index, stop)) is not None:
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

    def _link(self, index: int, stop: int) -> re.Match[str] | None:
        """Return the inline or defined reference link starting at *index*."""
        text = self.text
        if (link := LINK.match(text, index, stop)) is not None:
            return link
        if (full := FULL_REFERENCE.match(text, index, stop)) is not None:
            label = full.group(2) or full.group(1)
            return full if normalize_label(label) in self.labels else None
        short = SHORTCUT_REFERENCE.match(text, index, stop)
        if short is not None and normalize_label(short.group(1)) in self.labels:
            return short
        return None

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
    # Six buckets per character keep incompatible dual-purpose runs available
    # for later closers without repeatedly scanning them (quadratic on hostile
    # input). Pairing uses the original run lengths, even after partial use.
    openers: dict[str, dict[tuple[bool, int], list[tuple[int, _Delimiter]]]] = {
        char: {(closes, mod): [] for closes in (False, True) for mod in range(3)}
        for char in "*_"
    }
    for index, token in enumerate(tokens):
        if not isinstance(token, _Delimiter):
            continue
        buckets = openers[token.char]
        while token.can_close and token.count:
            candidates = [
                stack[-1]
                for (closes, mod), stack in buckets.items()
                if stack
                and not (
                    (closes or token.can_open)
                    and (mod + token.length) % 3 == 0
                    and (mod != 0 or token.length % 3 != 0)
                )
            ]
            if not candidates:
                break
            position, opener = max(candidates, key=operator.itemgetter(0))
            # Openers between a matched pair cannot participate in a later
            # crossing pair. Each discarded run is popped only once.
            for stack in buckets.values():
                while stack and stack[-1][0] > position:
                    stack.pop()
            strong = opener.count >= STRONG and token.count >= STRONG
            used = STRONG if strong else 1
            opener.count -= used
            token.count -= used
            opener.opens = True
            if not opener.count:
                buckets[(opener.can_close, opener.length % 3)].pop()
        if token.can_open and token.count:
            buckets[(token.can_close, token.length % 3)].append((index, token))


def heading_text(text: str, labels: frozenset[str] = frozenset()) -> str:
    """Return the text mask names a heading by, from its raw inline markdown.

    *labels* are the document's link reference definitions, normalized.
    """
    tokens = _Tokenizer(text, labels).tokens(0, len(text))
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
