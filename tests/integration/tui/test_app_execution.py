import sys
from pathlib import Path

import pytest

from nur.models import Task
from nur.registry import Registry
from nur.tui.app import NurApp


def _registry(argv):
    return Registry([Task(name="go", prefix="py", argv_base=tuple(argv))])


async def _wait_until_done(app, pilot) -> None:
    for _ in range(100):
        if not app._task_running:
            return
        await pilot.pause()
    pytest.fail("task did not finish within the pause budget")


@pytest.mark.asyncio
async def test_run_streams_output_and_sets_status() -> None:
    argv = [sys.executable, "-c", "print('hello-from-task')"]
    app = NurApp(_registry(argv), Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        await _wait_until_done(app, pilot)
        await pilot.pause()
        assert "hello-from-task" in "\n".join(app._output_lines)
        assert "exited(0)" in app._status_text


@pytest.mark.asyncio
async def test_interrupt_stops_running_task() -> None:
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    app = NurApp(_registry(argv), Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")
        for _ in range(100):
            if app._task_running:
                break
            await pilot.pause()
        assert app._task_running
        app.action_interrupt()
        await _wait_until_done(app, pilot)
        await pilot.pause()
        assert not app._task_running
        status = app._status_text
        assert status.startswith("exited(")


@pytest.mark.asyncio
async def test_second_run_blocked_while_running() -> None:
    app = NurApp(_registry([sys.executable, "-c", "pass"]), Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._task_running = True  # simulate an in-flight run
        before = app._status_text
        await pilot.press("r")
        await pilot.pause()
        assert app._status_text == before
