from __future__ import annotations

import difflib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nur.models import Task


class ResolutionError(Exception):
    """Base class for name-resolution failures."""


class UnknownTaskError(ResolutionError):
    def __init__(self, query: str, suggestions: list[str]) -> None:
        self.query = query
        self.suggestions = suggestions
        super().__init__(f"unknown task: {query!r}")


class AmbiguousTaskError(ResolutionError):
    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        super().__init__(f"ambiguous task: {name!r}")


class Registry:
    def __init__(self, tasks: list[Task]) -> None:
        self._tasks = tasks

    def is_empty(self) -> bool:
        return not self._tasks

    def all(self) -> list[Task]:
        return list(self._tasks)

    def groups(self) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for t in self._tasks:
            out.setdefault(t.prefix, []).append(t)
        return out

    def _suggest(self, query: str) -> list[str]:
        pool = [t.name for t in self._tasks] + [t.qualified_name for t in self._tasks]
        return difflib.get_close_matches(query, pool, n=3, cutoff=0.5)

    def resolve(self, query: str) -> Task:
        known_prefixes = {t.prefix for t in self._tasks}
        if ":" in query and query.split(":", 1)[0] in known_prefixes:
            prefix, _, name = query.partition(":")
            for t in self._tasks:
                if t.prefix == prefix and t.name == name:
                    return t
            raise UnknownTaskError(query, self._suggest(query))
        matches = [t for t in self._tasks if t.name == query]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise UnknownTaskError(query, self._suggest(query))
        raise AmbiguousTaskError(query, [t.qualified_name for t in matches])
