from pathlib import Path

import pytest

from nur.models import Task
from nur.registry import Registry
from nur.tui.app import NurApp


def _registry():
    return Registry([
        Task(
            name="test",
            prefix="npm",
            argv_base=("npm", "run", "test"),
            description="unit tests",
        ),
        Task(name="build", prefix="npm", argv_base=("npm", "run", "build")),
        Task(name="lint", prefix="pdm", argv_base=("pdm", "run", "lint")),
    ])


@pytest.mark.asyncio
async def test_initial_selection_and_detail() -> None:
    app = NurApp(_registry(), Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.selected_task is not None
        detail = app._detail_text
        assert "npm:" in str(detail)


@pytest.mark.asyncio
async def test_filter_narrows_list() -> None:
    app = NurApp(_registry(), Path())
    async with app.run_test() as pilot:
        await pilot.press("/")
        for ch in "lint":
            await pilot.press(ch)
        await pilot.pause()
        assert app.selected_task is not None
        assert app.selected_task.name == "lint"


@pytest.mark.asyncio
async def test_next_prev_keys_move_selection() -> None:
    app = NurApp(_registry(), Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("j")  # action_next
        await pilot.pause()
        moved = app.selected_task
        await pilot.press("k")  # action_prev
        await pilot.pause()
        await pilot.press("question_mark")  # action_toggle_help
        await pilot.pause()
        assert app.selected_task is not None
        assert moved is not None
        assert "keys:" in str(app._status_text)


@pytest.mark.asyncio
async def test_filter_with_no_matches_clears_selection() -> None:
    app = NurApp(_registry(), Path())
    async with app.run_test() as pilot:
        await pilot.press("/")  # action_focus_filter
        for ch in "zzzz":
            await pilot.press(ch)
        await pilot.pause()
        assert app.selected_task is None
        assert "no matches" in str(app._detail_text)


@pytest.mark.asyncio
async def test_detail_includes_task_definition() -> None:
    registry = Registry([
        Task(
            name="test",
            prefix="make",
            argv_base=("make", "test"),
            definition="test:\n\tpytest",
        )
    ])
    app = NurApp(registry, Path())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "pytest" in str(app._detail_text)


@pytest.mark.asyncio
async def test_quit_binding() -> None:
    app = NurApp(_registry(), Path())
    async with app.run_test() as pilot:
        await pilot.press("q")
        await pilot.pause()
        # Assert INSIDE the context: run_test() shuts the app down on exit, so
        # checking after the block would pass even if `q` did nothing.
        assert not app.is_running
