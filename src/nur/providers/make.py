from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger("nur")

# A rule's left-hand side: one or more target names before ':' or '::'.
# The negative lookahead excludes variable assignments (':=', '='); recipe
# lines are tab-indented so they never start with [A-Za-z0-9].
_RULE_RE = re.compile(r"^([A-Za-z0-9][^:=#]*?)\s*::?(?!=)")
# A single valid target name (excludes '%' pattern rules and '.'-prefixed).
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
# An inline "## description" anywhere on the line.
_DESC_RE = re.compile(r"##\s*(.*?)\s*$")


def _targets_on_line(line: str) -> tuple[list[str], str | None]:
    """Return (target names, description) for one Makefile line.

    Handles multiple targets sharing a rule, e.g. ``build test: ## desc``.
    """
    match = _RULE_RE.match(line)
    if not match:
        return [], None
    names = [
        token
        for token in match.group(1).split()
        if _NAME_RE.match(token) and token != "Makefile"
    ]
    desc_match = _DESC_RE.search(line)
    return names, (desc_match.group(1) if desc_match else None)


def parse_targets(text: str) -> list[str]:
    """Extract target names from Makefile *text* without executing anything.

    Deliberately does NOT shell out to ``make``: the database dump
    (``make -pRrq``) still evaluates ``$(shell ...)`` / ``!=`` assignments while
    reading the file, so a repository's Makefile could run arbitrary commands
    merely by discovering/listing tasks. Text parsing is safe, at the cost of
    not resolving ``include`` directives or computed targets.
    """
    names: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        for name in _targets_on_line(line)[0]:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def parse_descriptions(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        names, desc = _targets_on_line(line)
        if desc:
            for name in names:
                out.setdefault(name, desc)
    return out


class MakeProvider:
    prefix = "make"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "Makefile").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / "Makefile").read_text()
        except OSError as exc:
            log.warning("nur: skipping Makefile (%s)", exc)
            return []
        descriptions = parse_descriptions(text)
        return [
            Task(
                name=name,
                prefix=self.prefix,
                argv_base=("make", name),
                description=descriptions.get(name),
                source_file="Makefile",
            )
            for name in parse_targets(text)
        ]
