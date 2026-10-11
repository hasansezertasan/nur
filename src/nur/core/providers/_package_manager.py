"""Resolve the JavaScript package manager from lockfiles shared by providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["LOCKFILES", "resolve_pm"]

# (lockfile, package manager) in precedence order. Bun writes a text `bun.lock`
# by default since 1.2 and the legacy binary `bun.lockb` with `--save-text-lockfile`
# disabled; recognize both.
LOCKFILES: tuple[tuple[str, str], ...] = (
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)


def resolve_pm(cwd: Path) -> str:
    """Return the package manager owning ``cwd`` (``npm`` when unknown)."""
    for lockfile, pm in LOCKFILES:
        if (cwd / lockfile).exists():
            return pm
    return "npm"
