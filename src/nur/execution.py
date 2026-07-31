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

    from nur.models import Task

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
        # start_new_session=True puts the child in its own process group so an
        # interrupt can signal the whole tree (e.g. npm -> node, make -> sh),
        # not just the runner PID.
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
                start_new_session=True,
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
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        except OSError:
            # Process already gone (ProcessLookupError), or the group could not
            # be signalled (PermissionError) -- both subclass OSError;
            # fall back to signalling the process directly.
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.send_signal(signal.SIGINT)
