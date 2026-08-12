from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from nur.core.models import Task

__all__ = ["ProcessRunner", "run_direct"]


# Conventional "command not found" exit status (as used by POSIX shells).
RUNNER_NOT_FOUND = 127


def run_direct(task: Task, extra_args: list[str], cwd: Path) -> int:
    """Run a task with inherited stdio; return the child's exit code.

    A natively-parsed provider (npm/PDM/poe) can surface a task whose runner
    binary is not installed. Rather than letting ``FileNotFoundError`` escape
    as a traceback, report a controlled error and return ``127``.
    """
    argv = task.run_argv(extra_args)
    try:
        completed = subprocess.run(argv, cwd=cwd, check=False)
    except FileNotFoundError:
        print(
            f"nur: runner '{argv[0]}' is not installed "
            f"(needed to run {task.qualified_name}).",
            file=sys.stderr,
        )
        return RUNNER_NOT_FOUND
    return completed.returncode


class ProcessRunner:
    """Runs a command with combined output captured and streamed line-by-line.

    Blocking; intended to be driven from a background thread by the TUI.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def run(self, argv: list[str], cwd: Path, on_line: Callable[[str], None]) -> int:
        # Put the child in its own process group so an interrupt can signal the
        # whole tree (e.g. npm -> node, make -> sh), not just the runner PID:
        # start_new_session on POSIX, CREATE_NEW_PROCESS_GROUP on Windows. Each
        # flag is a no-op on the other platform, so both are always passed.
        creationflags = 0
        start_new_session = False
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            start_new_session = True
        # Not a `with` block: the process is stored on self and outlives this
        # method so interrupt() can signal it; cleanup happens in the finally.
        try:
            proc = subprocess.Popen(  # pylint: disable=consider-using-with
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except FileNotFoundError:
            # Natively-discovered task whose runner isn't installed: surface a
            # message in the output pane instead of an empty pane + exited(1).
            on_line(f"nur: runner '{argv[0]}' is not installed.\n")
            return RUNNER_NOT_FOUND
        with self._lock:
            self._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                on_line(line)
        finally:
            # Runs even if on_line raises, so we never leak the pipe or the
            # child and never leave stale runner state behind.
            # stdout is always a PIPE here, so the None branch never runs.
            if proc.stdout is not None:  # pragma: no branch
                proc.stdout.close()
            code = proc.wait()
            with self._lock:
                self._proc = None
        return code

    # interrupt() drives OS signals against a live child, so it is exercised
    # manually (see tests/tui/test_app_execution.py) rather than in unit tests.
    def interrupt(self) -> None:  # pragma: no cover
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        # if/else (not an early return) so type checkers treat each arm as a
        # platform-specific branch and don't flag the other as unreachable.
        if sys.platform == "win32":
            # Windows has no os.killpg; CTRL_BREAK is the documented way to
            # interrupt a CREATE_NEW_PROCESS_GROUP child (and, unlike CTRL_C,
            # it targets only the child's group, never nur's own console).
            with contextlib.suppress(OSError):
                proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            try:
                # os.killpg/getpgid are POSIX-only (guarded by the branch above);
                # pylint's Windows `os` stub lacks them, so silence no-member there.
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)  # pylint: disable=no-member
            except OSError:
                # Process already gone (ProcessLookupError), or the group could
                # not be signalled (PermissionError) -- both subclass OSError;
                # fall back to signalling the process directly.
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.send_signal(signal.SIGINT)
