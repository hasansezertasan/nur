"""Discover Nx shared tasks by parsing nx.json, without running nx."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nur.core.models import Task
from nur.core.providers._jsonc import loads_jsonc

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["NxProvider", "parse_nx"]

log = logging.getLogger("nur")
_SOURCE_FILE = "nx.json"


def _target_names(data: object) -> dict[str, object] | None:
    # The root nx.json carries target *defaults*, not the per-project task
    # list (those live in project.json files and plugin-inferred package.json
    # scripts, which are out of scope). The defaults keys name the tasks
    # shared across projects, runnable via `nx run-many -t <name>`.
    if not isinstance(data, dict):
        return None
    targets = data.get("targetDefaults")
    return targets if isinstance(targets, dict) else None


def parse_nx(text: str, source_file: str = _SOURCE_FILE) -> list[Task]:
    """Parse a workspace root's ``nx.json`` *text* without executing anything.

    Deliberately does NOT run ``nx show projects`` to enumerate tasks: that
    loads Nx plugins and infers targets, which can run repository-controlled
    code during discovery. Reading the ``targetDefaults`` keys directly is
    safe, at the cost of missing per-project-only targets. Results are
    syntactic candidates, not a promise that every project implements them.
    """
    targets_table = _target_names(loads_jsonc(text, source_file))
    if not targets_table:
        return []
    tasks: list[Task] = []
    for key, value in targets_table.items():
        if not isinstance(value, dict):
            continue
        name = str(key)
        if not name:
            continue
        tasks.append(
            Task(
                name=name,
                prefix="nx",
                argv_base=("nx", "run-many", "-t", name),
                source_file=source_file,
            )
        )
    return tasks


class NxProvider:
    prefix = "nx"

    def detect(self, cwd: Path) -> bool:
        return (cwd / _SOURCE_FILE).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / _SOURCE_FILE).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            log.warning("nur: skipping %s (%s)", _SOURCE_FILE, exc)
            return []
        return parse_nx(text)
