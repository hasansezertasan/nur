"""String-aware JSONC helpers shared by the JSON-with-comments providers.

Several JavaScript-ecosystem configs (``turbo.json``, ``nx.json``) permit
``//`` and ``/* */`` comments plus trailing commas, which stdlib
``json.loads`` rejects. This module strips those (without touching comment
markers or commas inside string literals) so the result parses as plain JSON.
"""

from __future__ import annotations

import json
import logging

__all__ = ["loads_jsonc"]

log = logging.getLogger("nur")


def _copy_string(text: str, start: int, out: list[str]) -> int:
    """Copy a string literal (from its opening quote); return the index after it."""
    out.append(text[start])
    i, n = start + 1, len(text)
    while i < n:
        char = text[i]
        out.append(char)
        if char == "\\" and i + 1 < n:  # copy the escaped char verbatim
            out.append(text[i + 1])
            i += 2
            continue
        i += 1
        if char == '"':
            break
    return i


def _skip_comment(text: str, i: int) -> int | None:
    """If a comment starts at ``i``, return the index past it; otherwise None."""
    if text.startswith("//", i):
        end = text.find("\n", i + 2)
        return len(text) if end == -1 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        if end == -1:  # an unterminated block comment is malformed JSONC
            msg = "unterminated block comment"
            raise ValueError(msg)
        return end + 2
    return None


def _drop_trailing_comma(out: list[str]) -> None:
    """Remove a trailing comma (and any whitespace) before a closing ``}``/``]``."""
    j = len(out) - 1
    while j >= 0 and out[j] in " \t\r\n":
        j -= 1
    if j >= 0 and out[j] == ",":
        del out[j:]


def strip_jsonc(text: str) -> str:
    """Remove ``//``/``/* */`` comments and trailing commas from JSONC text."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            i = _copy_string(text, i, out)
            continue
        skipped = _skip_comment(text, i)
        if skipped is not None:
            i = skipped
            continue
        if char in "}]":
            _drop_trailing_comma(out)
        out.append(char)
        i += 1
    return "".join(out)


def loads_jsonc(text: str, source_file: str) -> object:
    """Parse JSONC ``text``; on failure log a warning and return None."""
    try:
        return json.loads(strip_jsonc(text))
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("nur: skipping %s (%s)", source_file, exc)
        return None
