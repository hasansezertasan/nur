from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    prefix: str
    argv_base: tuple[str, ...]
    description: str | None = None
    definition: str = ""
    source_file: str = ""
    # Tokens inserted before passthrough args when (and only when) extra args
    # are forwarded, e.g. npm requires `npm run <script> -- <args>`.
    passthrough_prefix: tuple[str, ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.prefix}:{self.name}"

    def run_argv(self, extra_args: list[str] | None = None) -> list[str]:
        extra = extra_args or []
        separator = self.passthrough_prefix if extra else ()
        return [*self.argv_base, *separator, *extra]


@runtime_checkable
class Provider(Protocol):
    prefix: str

    def detect(self, cwd: Path) -> bool: ...

    def discover(self, cwd: Path) -> list[Task]: ...
