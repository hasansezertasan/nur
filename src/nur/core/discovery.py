from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nur.core.providers import PROVIDERS
from nur.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path

    from nur.core.models import Provider, Task

__all__ = ["discover"]


log = logging.getLogger("nur")


def discover(cwd: Path, providers: list[Provider] | None = None) -> Registry:
    providers = PROVIDERS if providers is None else providers
    tasks: list[Task] = []
    for provider in providers:
        try:
            if provider.detect(cwd):
                tasks.extend(provider.discover(cwd))
        # Backstop: a provider must never break discovery for the others.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            log.warning("nur: provider %r failed (%s)", provider.prefix, exc)
    return Registry(tasks)
