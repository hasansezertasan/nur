from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import yaml

from nur.core.models import Task

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["PreCommitProvider", "parse_pre_commit_config"]


log = logging.getLogger("nur")


def parse_pre_commit_config(
    text: str, source_file: str = ".pre-commit-config.yaml"
) -> list[Task]:
    """Parse a pre-commit config without invoking ``pre-commit``.

    Only ``repos[].hooks[]`` declares hooks.  In particular, this deliberately
    does not walk arbitrary YAML mappings, whose ``id`` keys can be unrelated
    configuration.
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return []
    repos = data.get("repos")
    if not isinstance(repos, list):
        return []

    tasks: list[Task] = []
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        hooks = repo.get("hooks")
        if not isinstance(hooks, list):
            continue
        is_local = repo.get("repo") == "local"
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            hook_id = hook.get("id")
            if not isinstance(hook_id, str) or not hook_id:
                continue
            label = hook.get("name")
            entry = hook.get("entry") if is_local else None
            tasks.append(
                Task(
                    name=hook_id,
                    prefix="pre-commit",
                    argv_base=("pre-commit", "run", hook_id, "--all-files"),
                    description=label if isinstance(label, str) else None,
                    definition=entry if isinstance(entry, str) else "",
                    source_file=source_file,
                )
            )
    return tasks


class PreCommitProvider:
    prefix = "pre-commit"
    source_file = ".pre-commit-config.yaml"

    def detect(self, cwd: Path) -> bool:
        return (cwd / self.source_file).is_file()

    def discover(self, cwd: Path) -> list[Task]:
        try:
            text = (cwd / self.source_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("nur: skipping %s (%s)", self.source_file, exc)
            return []
        try:
            return parse_pre_commit_config(text, self.source_file)
        except yaml.YAMLError as exc:
            log.warning("nur: skipping %s (%s)", self.source_file, exc)
            return []
