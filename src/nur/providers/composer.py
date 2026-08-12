from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from nur.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ComposerProvider"]


log = logging.getLogger("nur")

CONFIG_FILE = "composer.json"

# Composer reserves these event names for its own lifecycle: they fire
# automatically during install/update/etc. and are not user-invocable custom
# scripts, so we drop them from discovery to keep the picker uncluttered.
# Command, installer, package, and plugin events per the scripts article:
# https://getcomposer.org/doc/articles/scripts.md
RESERVED_EVENTS = frozenset({
    # Command events
    "pre-install-cmd",
    "post-install-cmd",
    "pre-update-cmd",
    "post-update-cmd",
    "pre-status-cmd",
    "post-status-cmd",
    "pre-archive-cmd",
    "post-archive-cmd",
    "pre-autoload-dump",
    "post-autoload-dump",
    "post-root-package-install",
    "post-create-project-cmd",
    # Installer events
    "pre-operations-exec",
    # Package events
    "pre-package-install",
    "post-package-install",
    "pre-package-update",
    "post-package-update",
    "pre-package-uninstall",
    "post-package-uninstall",
    # Plugin events
    "init",
    "command",
    "pre-file-download",
    "post-file-download",
    "pre-command-run",
    "pre-pool-create",
})


def _load(cwd: Path) -> tuple[dict[str, object], dict[str, object]] | None:
    """Return ``(scripts, scripts-descriptions)``, or None if unavailable.

    None means composer.json is absent, unreadable, malformed, or carries no
    ``scripts`` object; a present-but-empty descriptions table yields ``{}``.
    """
    path = cwd / CONFIG_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", CONFIG_FILE, exc)
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return None
    descriptions = data.get("scripts-descriptions")
    return scripts, descriptions if isinstance(descriptions, dict) else {}


def _definition(value: object) -> str:
    # A script entry is either a single string or an array of entries run in
    # sequence (each a CLI command, a PHP static-method callback, or an
    # ``@``-reference/directive). nur can't meaningfully render callbacks or
    # ``@``-tokens as a shell command, so entries are surfaced verbatim: strings
    # joined with ` && `, opaque values left as-is.
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " && ".join(entry for entry in value if isinstance(entry, str))
    return ""


class ComposerProvider:
    prefix = "composer"

    def detect(self, cwd: Path) -> bool:
        loaded = _load(cwd)
        if loaded is None:
            return False
        scripts, _ = loaded
        return any(name not in RESERVED_EVENTS for name in scripts)

    def discover(self, cwd: Path) -> list[Task]:
        loaded = _load(cwd)
        if loaded is None:
            return []
        scripts, descriptions = loaded
        tasks: list[Task] = []
        for name, value in scripts.items():
            # Surface only user-defined custom scripts; Composer's reserved
            # lifecycle/event hooks are internals, not runnable tasks.
            if name in RESERVED_EVENTS:
                continue
            # A script value is a string or an array; skip anything else
            # (numbers, objects, booleans) as malformed.
            if not isinstance(value, (str, list)):
                continue
            desc = descriptions.get(name)
            tasks.append(
                Task(
                    name=name,
                    prefix=self.prefix,
                    argv_base=("composer", "run-script", name),
                    description=desc if isinstance(desc, str) else None,
                    definition=_definition(value),
                    source_file=CONFIG_FILE,
                )
            )
        return tasks
