from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger("nur")

# (lockfile, package manager) in precedence order. Bun writes a text `bun.lock`
# by default since 1.2 and the legacy binary `bun.lockb` with `--save-text-lockfile`
# disabled; recognize both.
_LOCKFILES: tuple[tuple[str, str], ...] = (
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)


def resolve_pm(cwd: Path) -> str:
    for lockfile, pm in _LOCKFILES:
        if (cwd / lockfile).exists():
            return pm
    return "npm"


class NpmProvider:
    prefix = "npm"

    def detect(self, cwd: Path) -> bool:
        return (cwd / "package.json").is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            data = json.loads((cwd / "package.json").read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("nur: skipping package.json (%s)", exc)
            return []
        # Annotated Any (not dict) so the runtime isinstance guard below stays
        # meaningful: package.json "scripts" may not be an object.
        scripts: Any = data.get("scripts") or {}
        if not isinstance(scripts, dict):
            log.warning("nur: skipping package.json ('scripts' is not an object)")
            return []
        pm = resolve_pm(cwd)
        return [
            Task(
                name=name,
                prefix=self.prefix,
                argv_base=(pm, "run", name),
                definition=str(command),
                source_file="package.json",
                # npm requires `npm run <script> -- <args>`; pnpm/yarn/bun accept
                # the `--` separator too, so emit it uniformly when forwarding.
                passthrough_prefix=("--",),
            )
            for name, command in scripts.items()
        ]
