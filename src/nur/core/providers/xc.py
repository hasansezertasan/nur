from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._markdown import HEADING, code_lines, fence_blocks

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["XcProvider", "parse_xc"]


log = logging.getLogger("nur")

SOURCE_FILE = "README.md"


# Like a heading, the marker may carry up to three leading spaces.
MARKER_COMMENT = re.compile(r"^ {0,3}<!-- xc-heading -->\s*$")
# Four or more leading spaces (or a leading tab) is an indented code block, not
# prose -- such lines must not be folded into a task's description.
INDENTED_CODE = re.compile(r"^(?: {4,}|\t)")
# Leading/trailing code-span and emphasis markers around a heading's text. `_`
# and `~` are left alone: both are legal inside an xc task name.
INLINE_MARKUP = re.compile(r"^[`*]+|[`*]+$")
# Attribute lines sit between a task heading and its script. nur does not
# interpret them -- `xc <name>` applies them at run time -- but they must not
# leak into the task description.
ATTRIBUTE = re.compile(
    r"^(requires|req|dir|directory|env|environment|inputs|run|rundeps|interactive)\s*:",
    re.IGNORECASE,
)


def _heading(line: str) -> tuple[int, str] | None:
    match = HEADING.match(line)
    if match is None:
        return None
    hashes, text = match.groups()
    if text is None:
        # xc needs a space after the hashes, so a bare `##` line is prose.
        return None
    text = text.strip().rstrip("#").strip()
    # xc reads heading text from the markdown AST, so a code span or emphasis
    # around the name is markup, not part of it: ``### `build` `` is `build`.
    # copier-pyproject writes every task heading as an inline code span.
    return len(hashes), INLINE_MARKUP.sub("", text)


def _find_section(lines: list[str], code: set[int]) -> tuple[int, int] | None:
    """Return ``(section level, first body line)`` for the task-list heading.

    xc's priority: a heading preceded by ``<!-- xc-heading -->`` wins over a
    heading literally named ``Tasks``. Only the first match of either kind is
    used -- xc ignores any later task-list section.
    """
    marked = False
    fallback: tuple[int, int] | None = None
    for index, line in enumerate(lines):
        if index in code:
            marked = False  # a fenced block breaks marker-to-heading adjacency
            continue
        heading = _heading(line)
        if heading is None:
            if MARKER_COMMENT.match(line):
                marked = True
            elif line.strip():
                marked = False  # only blank lines may sit between marker and heading
            continue
        if marked:
            return heading[0], index + 1
        if fallback is None and heading[1].lower() == "tasks":
            fallback = (heading[0], index + 1)
    return fallback


def _definition(
    lines: list[str], blocks: list[tuple[int, int]], start: int, end: int
) -> str:
    """Return the first fenced block in ``lines[start:end]``, fences excluded."""
    for open_index, close_index in blocks:
        if start <= open_index < end:
            return "\n".join(lines[open_index + 1 : min(close_index, end)])
    return ""


def _description(
    lines: list[str], blocks: list[tuple[int, int]], start: int, end: int
) -> str | None:
    """Return the prose between a task heading and its script, sans attributes."""
    limit = next(
        (open_index for open_index, _ in blocks if start <= open_index < end), end
    )
    prose = [
        stripped
        for line in lines[start:limit]
        if (stripped := line.strip())
        and not ATTRIBUTE.match(stripped)
        and not INDENTED_CODE.match(line)
    ]
    return " ".join(prose) or None


def parse_xc(text: str, source_file: str = SOURCE_FILE) -> list[Task]:
    """Parse xc tasks out of a markdown *text* without executing anything.

    Deliberately does NOT run ``xc -s``: xc executes task scripts, and listing
    should never be able to run repository-controlled commands. The grammar is
    documented at https://xcfile.dev/task-syntax/task-list/.
    """
    lines = text.splitlines()
    blocks = fence_blocks(lines)
    code = code_lines(lines, blocks)
    section = _find_section(lines, code)
    if section is None:
        return []
    section_level, start = section

    end = len(lines)
    for index in range(start, len(lines)):
        heading = _heading(lines[index]) if index not in code else None
        if heading is not None and heading[0] <= section_level:
            end = index
            break

    heads = [
        (index, heading[1])
        for index in range(start, end)
        if index not in code
        and (heading := _heading(lines[index])) is not None
        and heading[0] == section_level + 1
    ]

    tasks: list[Task] = []
    for position, (index, name) in enumerate(heads):
        body_end = heads[position + 1][0] if position + 1 < len(heads) else end
        if not name or any(char.isspace() for char in name):
            # xc task names cannot contain whitespace, so this heading is prose.
            log.debug("nur: skipping xc heading %r (not a valid task name)", name)
            continue
        tasks.append(
            Task(
                name=name,
                prefix="xc",
                argv_base=("xc", name),
                description=_description(lines, blocks, index + 1, body_end),
                definition=_definition(lines, blocks, index + 1, body_end),
                source_file=source_file,
            )
        )
    return tasks


def _load_tasks(cwd: Path) -> list[Task]:
    path = cwd / SOURCE_FILE
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", SOURCE_FILE, exc)
        return []
    return parse_xc(text, SOURCE_FILE)


class XcProvider:
    prefix = "xc"

    def detect(self, cwd: Path) -> bool:
        # A README exists in nearly every repository, so file presence alone is
        # not evidence of an xc project -- require at least one parsed task.
        return bool(_load_tasks(cwd))

    def discover(self, cwd: Path) -> list[Task]:
        return _load_tasks(cwd)
