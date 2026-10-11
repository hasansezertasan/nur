from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nur.core.models import Task
from nur.core.providers._package_manager import resolve_pm

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["NpmProvider", "resolve_pm"]


log = logging.getLogger("nur")


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
