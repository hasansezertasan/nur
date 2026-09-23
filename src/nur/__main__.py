"""Runnable entrypoint for ``python -m nur`` and the ``nur`` console script.

This is the single runnable entrypoint used by ``python -m nur``
and by every standalone-executable build (PyCrucible launcher, PyInstaller
freezer, Nuitka compiler — see ADR-007). The build tools all target this file,
so the component-selection logic lives here and nowhere else.

``main()`` runs the ``nur`` console root (``nur.cli.main``), which runs a task
when one is named and launches the TUI when none is.

The binding routes its import through a loader that turns a missing
dependency into one actionable line instead of a traceback (ADR-028). This is
the boundary that needs it most: a standalone executable's user has no console
root to fall back on and no obvious way to read a Python stack trace.
"""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["main"]

# Each of these ships as a core dependency of this package, so a missing one
# never means "install an extra" -- it means this environment is out of sync with
# the installed metadata. A ``copier update`` that adds the shared launcher (or
# enables settings) adds both the code and its dependency at once, so an
# environment that has not been re-synced would otherwise fail here with a bare
# ``ModuleNotFoundError`` before any launcher code executes.
_ROOT_DEPENDENCIES = ("typer",)
_MISSING_ROOT_DEPENDENCY = "Error: The nur command requires the '{missing}' package, which is not installed. It ships with 'nur', so this usually means your environment is out of sync -- run `uv sync` (or reinstall the package) and try again."  # noqa: E501


def _preflight(module: str) -> None:
    """Import a dependency eagerly, attributing nested failures to it.

    Args:
        module: Import module name to verify eagerly.

    Raises:
        ModuleNotFoundError: Naming ``module`` when it cannot be imported.
    """
    try:
        _ = importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(str(exc), name=module) from None
    except ImportError as exc:
        raise ModuleNotFoundError(str(exc), name=module) from None


def _load_console_root() -> Callable[[], int]:
    """Import the console root, reporting its own missing dependency actionably.

    Returns:
        Callable[[], int]: The console root, ``nur.cli.main``, which reads
            ``sys.argv`` when called without arguments and returns an exit code.

    Raises:
        ModuleNotFoundError: Propagating a missing module that is not exactly one
            of the root's own dependencies, so an unrelated import defect keeps
            its diagnostic context.
        SystemExit: With code 1 when one of them is missing from this environment.
    """
    try:
        for dependency in _ROOT_DEPENDENCIES:
            _preflight(dependency)
        from nur.cli import main as root  # ruff: ignore[import-outside-top-level]
    except ModuleNotFoundError as exc:
        missing = exc.name
        if missing is None or missing not in _ROOT_DEPENDENCIES:
            raise
        _ = sys.stderr.write(_MISSING_ROOT_DEPENDENCY.format(missing=missing) + "\n")
        raise SystemExit(1) from None
    return root


# The dispatchers below carry `# pragma: no cover`: invoking them launches the
# blocking component (CLI loop, GUI mainloop, server, ...), which cannot run
# under headless CI. tests/test_main.py pins the import wiring and callability.
def main() -> int:  # pragma: no cover
    """Run the nur console root and return its exit code.

    Returns:
        int: The process exit code.
    """
    return _load_console_root()()


if __name__ == "__main__":
    raise SystemExit(main())  # pragma: no cover - exercised via subprocess only
