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
from html.entities import html5
from typing import NamedTuple

__all__ = [
    "DEFINITION",
    "DEFINITION_TARGET",
    "DEFINITION_TITLE",
    "heading_text",
    "normalize_label",
    "valid_definition_target",
    "valid_reference_label",
]


ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
REFERENCE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});"
)
# Link destinations may contain arbitrarily nested balanced parentheses.
# Titles are quoted or parenthesized, with punctuation escapes.
_TITLE = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\))"""
ANGLE_DESTINATION = re.compile(r"<(?:[^<>\n\\]|\\.)*>")
LINK_TITLE = re.compile(_TITLE)
EMPTY_DESTINATION_TITLE = re.compile(rf"{_TITLE}\s*\)", re.ASCII)
DEFINITION_TITLE = re.compile(rf"^[ \t]*{_TITLE}[ \t]*$")
_LINK_CHAR = r"(?:[^\[\]\\]|\\.)"
_LINK_TEXT = rf"{_LINK_CHAR}*"
REFERENCE_LABEL = re.compile(rf"\[({_LINK_TEXT})\]")
# A link reference definition line, `[label]: destination`, and what may follow
# its colon (here or on the next line): a destination and an optional title.
DEFINITION = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.)+)\]:(?:[ \t]|$)")
DEFINITION_TARGET = re.compile(
    rf"""^[ \t]*({ANGLE_DESTINATION.pattern}|[^\s<]\S*)(?:[ \t]+"""
    r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\((?:[^()\\]|\\.)*\)))?[ \t]*$""",
    re.ASCII,
)
# CommonMark URI schemes have 2--32 characters; autolinks exclude spaces,
# controls, and angle brackets. Email domains contain DNS-style labels.
_DOMAIN_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
AUTOLINK = re.compile(
    r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^<>\x00-\x20]*"
    rf"|[A-Za-z0-9.!#$%&'*+/=?^_`{{|}}~-]+@{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})*)>"
)
BACKTICKS = re.compile(r"`+")
LINK_WHITESPACE = " \t\r\n\f\v"
MAX_REFERENCE_LABEL = 999
CONTROL_LIMIT = 0x20
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


def _reference_text(reference: str) -> str:
    """Decode numeric references and only exact named entity entries."""
    if reference.startswith("&#"):
        return html.unescape(reference)
    return html5.get(reference[1:], reference)


def normalize_label(label: str) -> str:
    """Return the form two link labels must share to match: case and spacing fold."""
    return " ".join(label.split()).casefold()


def valid_reference_label(label: str) -> bool:
    """Apply the reference-label limit used by Mask's pulldown-cmark version."""
    # Its ASCII fast path is unlimited; non-ASCII labels have a UTF-8 byte limit.
    return bool(normalize_label(label)) and (
        label.isascii() or len(label.encode("utf-8")) <= MAX_REFERENCE_LABEL
    )


def valid_definition_target(target: str) -> bool:
    """Whether mask accepts a reference destination and its optional title."""
    match = DEFINITION_TARGET.match(target)
    if match is None:
        return False
    destination = match.group(1)
    if destination.startswith("<"):
        return True
    if any(ord(char) < CONTROL_LIMIT for char in destination):
        return False
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


class _Link(NamedTuple):
    start: int
    stop: int
    end: int


class _Tokenizer:
    def __init__(self, text: str, labels: frozenset[str]) -> None:
        self.text = text
        self.labels = labels
        # Backtick runs by length, so a code span finds its closer by bisection
        # instead of rescanning the rest of the heading.
        self.runs: dict[int, list[int]] = {}
        for run in BACKTICKS.finditer(text):
            self.runs.setdefault(len(run.group()), []).append(run.start())
        self.nested: set[int] = set()
        self.images: set[int] = set()
        self.brackets = self._bracket_pairs()
        self.parens, self.breaks = self._parenthesis_pairs()
        self.links = self._matched_links()

    def _bracket_pairs(self) -> dict[int, int]:
        """Pair brackets once, skipping escaped brackets and code spans."""
        pairs: dict[int, int] = {}
        stack: list[int] = []
        index = 0
        escaped_bangs: set[int] = set()
        while index < len(self.text):
            if (escape := ESCAPED.match(self.text, index)) is not None:
                if escape.group(1) == "!":
                    escaped_bangs.add(escape.end() - 1)
                index = escape.end()
                continue
            char = self.text[index]
            if char == "`":
                index = self._code(index, len(self.text), [])
                continue
            if char == "<" and (autolink := AUTOLINK.match(self.text, index)):
                index = autolink.end()
                continue
            if char == "[":
                if (
                    index
                    and self.text[index - 1] == "!"
                    and index - 1 not in escaped_bangs
                ):
                    self.images.add(index)
                if stack:
                    self.nested.add(stack[-1])
                stack.append(index)
            elif char == "]" and stack:
                pairs[stack.pop()] = index
            index += 1
        return pairs

    def _parenthesis_pairs(self) -> tuple[dict[int, int], list[int]]:
        """Pair destination parentheses and count forbidden characters in spans."""
        pairs: dict[int, int] = {}
        stack: list[int] = []
        breaks = [0]
        index = 0
        while index < len(self.text):
            if (escape := ESCAPED.match(self.text, index)) is not None:
                breaks.extend([breaks[-1]] * (escape.end() - index))
                index = escape.end()
                continue
            char = self.text[index]
            breaks.append(
                breaks[-1] + int(char in LINK_WHITESPACE or ord(char) < CONTROL_LIMIT)
            )
            if char == "(":
                stack.append(index)
            elif char == ")" and stack:
                pairs[stack.pop()] = index
            index += 1
        return pairs, breaks

    def _target(self, opening: int, stop: int) -> int | None:
        """Return the end of a parenthesized link destination and optional title."""
        text = self.text
        if opening >= stop or text[opening] != "(":
            return None
        index = opening + 1
        while index < stop and text[index] in LINK_WHITESPACE:
            index += 1
        if index > opening + 1 and (
            title := EMPTY_DESTINATION_TITLE.match(text, index, stop)
        ):
            return title.end()
        angle = ANGLE_DESTINATION.match(text, index, stop)
        if angle is not None:
            index = angle.end()
        else:
            destination_end = self._bare_destination(index, stop)
            if destination_end is None:
                return None
            index = destination_end
        while index < stop and text[index] in LINK_WHITESPACE:
            index += 1
        if index < stop and text[index] == ")":
            return index + 1
        if (title := LINK_TITLE.match(text, index, stop)) is None:
            return None
        index = title.end()
        while index < stop and text[index] in LINK_WHITESPACE:
            index += 1
        return index + 1 if index < stop and text[index] == ")" else None

    def _bare_destination(self, index: int, stop: int) -> int | None:
        """Scan a bare destination, jumping balanced spans without rescanning."""
        if index < stop and self.text[index] == "<":
            return None
        while (
            index < stop
            and self.text[index] not in LINK_WHITESPACE
            and self.text[index] != ")"
        ):
            char = self.text[index]
            if (escape := ESCAPED.match(self.text, index, stop)) is not None:
                index = escape.end()
            elif char == "(":
                close = self.parens.get(index)
                if (
                    close is None
                    or close >= stop
                    or self.breaks[close] != self.breaks[index]
                ):
                    return None
                index = close + 1
            elif ord(char) < CONTROL_LIMIT:
                return None
            else:
                index += 1
        return index

    def _matched_links(self) -> dict[int, _Link]:
        """Form inner links first; each disables enclosing regular link openers."""
        links: dict[int, _Link] = {}
        last_link = -1
        # Bracket pairs are inserted in closing order.
        for bracket in self.brackets:
            candidate = self._raw_link(bracket, len(self.text))
            if candidate is None:
                continue
            image = bracket in self.images
            if not image and last_link > bracket:
                continue
            links[bracket] = candidate
            if not image:
                last_link = bracket
        return links

    def tokens(self, start: int, stop: int) -> list[_Token]:
        text, out, index = self.text, [], start
        spans: list[tuple[int, int]] = []
        while index < stop or spans:
            if index >= stop:
                index, stop = spans.pop()
                continue
            char = text[index]
            if (escaped := ESCAPED.match(text, index, stop)) is not None:
                out.append(escaped.group(1))
                index = escaped.end()
            elif (reference := REFERENCE.match(text, index, stop)) is not None:
                out.append(_reference_text(reference.group()))
                index = reference.end()
            elif char == "<" and (autolink := AUTOLINK.match(text, index, stop)):
                out.extend((None, autolink.group(1)))
                index = autolink.end()
            elif char == "`":
                index = self._code(index, stop, out)
            elif char in "[!" and (link := self._link(index, stop)) is not None:
                out.append(None)
                spans.append((link.end, stop))
                index, stop = link.start, link.stop
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

    def _link(self, index: int, stop: int) -> _Link | None:
        bracket = index + 1 if self.text[index] == "!" else index
        link = self.links.get(bracket)
        return link if link is not None and link.end <= stop else None

    def _raw_link(self, index: int, stop: int) -> _Link | None:
        """Return the inline or defined reference link starting at *index*."""
        text = self.text
        bracket = index + 1 if text[index] == "!" else index
        close = self.brackets.get(bracket)
        if close is None or close >= stop:
            return None
        if (target := self._target(close + 1, stop)) is not None:
            return _Link(bracket + 1, close, target)
        if (label := REFERENCE_LABEL.match(text, close + 1, stop)) is not None:
            name = label.group(1) or text[bracket + 1 : close]
            if valid_reference_label(name) and normalize_label(name) in self.labels:
                return _Link(bracket + 1, close, label.end())
            return None
        if (
            not self.labels
            or bracket in self.nested
            or (close + 1 < stop and text[close + 1] in "([")
        ):
            return None
        content = text[bracket + 1 : close]
        return (
            _Link(bracket + 1, close, close + 1)
            if valid_reference_label(content)
            and normalize_label(content) in self.labels
            else None
        )

    def _code(self, index: int, stop: int, out: list[_Token]) -> int:
        """Append the code span opening at *index*, or its backticks as text."""
        run = BACKTICKS.match(self.text, index)
        length = len(run.group()) if run is not None else 1
        runs = self.runs.get(length, [])
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
