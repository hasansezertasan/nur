from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["JustProvider", "parse_justfile"]


log = logging.getLogger("nur")

# A recipe header: an unindented line naming a recipe, e.g. ``@build arg='x': dep``.
# The leading ``@`` (quiet recipe) is optional; the name is captured. The negative
# lookahead after ``:`` rejects assignments (``foo := ...``), which is what keeps
# `alias b := build`, `export VERSION := ...`, and `set shell := [...]` out — their
# first token is matched as a name but the ``:=`` fails the lookahead.
_RECIPE_RE = re.compile(r"^@?([a-zA-Z_][a-zA-Z0-9_-]*)(?:\s+[^:]*?)?\s*:(?!=)")
# A ``[doc('text')]`` attribute (single or double quotes), possibly among others
# inside one bracket group, e.g. ``[private, doc("Internal helper")]``. The
# leading ``[`` or ``,`` anchors ``doc(`` to an actual attribute position, so a
# ``doc('...')`` substring inside another attribute's string argument (e.g.
# ``[confirm("... doc('x') ...")]``) is not mistaken for the description.
_DOC_ATTR_RE = re.compile(r"""[\[,]\s*doc\(\s*['"](.*?)['"]\s*\)""")
# Delimiters that open a *multiline* literal in just: triple-quoted strings and
# triple-backtick expressions. Lines inside such a literal can be unindented and
# look like recipe headers (``phantom:``), so they must be skipped.
_MULTILINE_DELIMS = ('"""', "'''", "```")


def _advance_multiline(line: str, state: str | None) -> str | None:
    """Return the open multiline delimiter after scanning *line* left to right.

    *state* is the delimiter open at the line's start (``None`` if not inside a
    literal). Toggling is intentionally naive — single-quote escaping is not
    modelled — which is adequate for keeping recipe-like lines inside triple
    literals from being mistaken for recipes.
    """
    index = 0
    while index < len(line):
        if state is None:
            opener = next(
                (d for d in _MULTILINE_DELIMS if line.startswith(d, index)), None
            )
            if opener is not None:
                state = opener
                index += len(opener)
                continue
        elif line.startswith(state, index):
            index += len(state)
            state = None
            continue
        index += 1
    return state


def parse_justfile(text: str, source_file: str = "justfile") -> list[Task]:
    """Extract recipes from ``justfile`` *text* without executing anything.

    Deliberately does NOT shell out to ``just --dump``: a pure text parse needs no
    ``just`` binary on PATH and cannot run recipe bodies. Descriptions come from the
    comment immediately preceding a recipe (skipping any attribute lines) or from an
    explicit ``[doc('...')]`` attribute. Grammar fidelity is intentionally partial:
    imports, string interpolation, and computed names are not resolved (see
    docs/adr/0001).
    """
    tasks: list[Task] = []
    seen: set[str] = set()
    pending_comment: str | None = None
    attr_doc: str | None = None
    in_delim: str | None = None
    for raw in text.splitlines():
        if in_delim is not None:
            # Inside a multiline literal; its lines are never recipe headers.
            in_delim = _advance_multiline(raw, in_delim)
            continue
        if not raw.strip():
            pending_comment = attr_doc = None
            continue
        if raw[0] in " \t":
            # Indented: a recipe body or dependency continuation; never a header.
            continue
        stripped = raw.strip()
        if stripped.startswith("#"):
            # A ``#!`` shebang is not a doc comment; drop it so it can't leak in
            # as the following recipe's description.
            pending_comment = (
                None if stripped.startswith("#!") else stripped[1:].strip() or None
            )
            continue
        if stripped.startswith("["):
            match = _DOC_ATTR_RE.search(stripped)
            if match:
                attr_doc = match.group(1)
            # Attributes sit between a doc comment and its recipe; keep the comment.
            continue
        match = _RECIPE_RE.match(raw)
        if match and (name := match.group(1)) not in seen:
            seen.add(name)
            tasks.append(
                Task(
                    name=name,
                    prefix="just",
                    argv_base=("just", name),
                    description=attr_doc or pending_comment,
                    source_file=source_file,
                )
            )
        # Any unindented, non-comment, non-attribute line ends the doc scope. It
        # may also open a multiline literal (e.g. ``x := \"\"\"``) whose body must
        # not be scanned for recipes.
        pending_comment = attr_doc = None
        in_delim = _advance_multiline(raw, None)
    return tasks


class JustProvider:
    prefix = "just"

    def _source_file(self, cwd: Path) -> str:
        return "justfile" if (cwd / "justfile").is_file() else "Justfile"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "justfile").is_file() or (cwd / "Justfile").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        source_file = self._source_file(cwd)
        try:
            text = (cwd / source_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping justfile (%s)", exc)
            return []
        return parse_justfile(text, source_file)
