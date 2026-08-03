import sys
import time
from pathlib import Path

import pytest

from nur.models import Task
from nur.registry import Registry
from nur.tui.app import NurApp


def _registry(argv):
    return Registry([Task(name="go", prefix="py", argv_base=tuple(argv))])


async def _wait_until(pilot, predicate, *, message: str) -> None:
    # Poll against a wall-clock deadline rather than counting event-loop pauses:
    # the child process is spawned and reaped on a background thread, so bare
    # pauses can spin faster than the OS starts/stops it and flake under load.
    # Returns as soon as the predicate holds, so the fast path stays fast.
    deadline = time.monotonic() + 15.0
    while True:
        if predicate():
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await pilot.pause(min(0.05, remaining))
    pytest.fail(message)


async def _wait_until_done(app, pilot) -> None:
    await _wait_until(
        pilot,
        lambda: not app._task_running,
        message="task did not finish within 15 seconds",
    )


async def _wait_scanned(app, pilot) -> None:
    await _wait_until(
        pilot,
        lambda: app._task_registry is not None and app.selected_task is not None,
        message="scan did not populate within 15 seconds",
    )


@pytest.mark.asyncio
async def test_run_streams_output_and_sets_status() -> None:
    argv = [sys.executable, "-c", "print('hello-from-task')"]
    app = NurApp(Path(), scan=lambda argv=argv: _registry(argv))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _wait_scanned(app, pilot)
        await pilot.press("r")
        await _wait_until_done(app, pilot)
        await pilot.pause()
        assert "hello-from-task" in "\n".join(app._output_lines)
        assert "exited(0)" in app._status_text


@pytest.mark.asyncio
async def test_interrupt_stops_running_task() -> None:
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    app = NurApp(Path(), scan=lambda argv=argv: _registry(argv))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _wait_scanned(app, pilot)
        await pilot.press("r")
        # action_run() sets _task_running synchronously but spawns the child on
        # a worker thread, so wait for the process itself to exist -- otherwise
        # interrupt() can race ahead of the spawn, no-op on a None proc, and
        # leave sleep(30) running until the deadline.
        await _wait_until(
            pilot,
            lambda: app._runner._proc is not None,
            message="task process did not start within 15 seconds",
        )
        assert app._task_running
        app.action_interrupt()
        await _wait_until_done(app, pilot)
        await pilot.pause()
        assert not app._task_running
        status = app._status_text
        assert status.startswith("exited(")


@pytest.mark.asyncio
async def test_second_run_blocked_while_running() -> None:
    app = NurApp(Path(), scan=lambda: _registry([sys.executable, "-c", "pass"]))
    async with app.run_test() as pilot:
        await pilot.pause()
        await _wait_scanned(app, pilot)
        app._task_running = True  # simulate an in-flight run
        before = app._status_text
        await pilot.press("r")
        await pilot.pause()
        assert app._status_text == before
