"""Run nur programmatically."""

from __future__ import annotations

from nur.cli import main


def list_tasks() -> int:
    """Print the tasks discovered in the current directory.

    Returns:
        int: The exit code ``nur list`` would return.
    """
    return main(["list"])
