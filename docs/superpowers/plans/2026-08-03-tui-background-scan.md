# TUI Background Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `nur` TUI appear instantly and run task discovery in a background thread, populating the list when the scan completes.

**Architecture:** Split "open the TUI" from "discover tasks." `cli.py` stops discovering on the no-argument path and calls `launch(cwd)` directly. `NurApp` takes a `scan` callable, starts empty, runs the scan in a Textual thread worker on mount, and rebuilds the list atomically when the worker finishes. The `just` provider gains a subprocess timeout so a hung scan cannot strand the TUI on `scanning…` forever.

**Tech Stack:** Python 3, Typer (CLI), Textual (TUI, thread workers via `run_worker`), pytest + pytest-asyncio (`app.run_test()` pilot harness).

## Global Constraints

- Python: use `from __future__ import annotations`; type-hint all new signatures.
- Style: follow PEP 8 and existing ruff/mypy/basedpyright gates. Lazy imports inside functions keep the `# noqa: PLC0415` marker as the codebase already does.
- No AI-authorship trailers in commits. Conventional Commits for messages.
- Never call `discover()` synchronously on the no-argument (TUI) CLI path — that is the entire point of this change.
- The three empty-ish list states must stay distinct: **scanning** (registry is `None`), **no tasks found** (registry present but empty), **no matches** (registry non-empty, filter excludes all).

---

### Task 1: Add a timeout to the `just` provider subprocess

**Files:**
- Modify: `src/nur/providers/just.py:42-58`
- Test: `tests/unit/providers/test_just.py` (create if absent; otherwise add to it)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `JustProvider.discover(cwd)` returns `[]` (not raise) when `just --dump` exceeds a 5-second timeout. No signature change.

- [ ] **Step 1: Write the failing test**

Check whether `tests/unit/providers/test_just.py` exists first. If it does, append the test function; if not, create the file with this content:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from nur.providers.just import JustProvider


def test_discover_returns_empty_on_timeout(monkeypatch) -> None:
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="just", timeout=5.0)

    monkeypatch.setattr(subprocess, "run", _raise_timeout)
    assert JustProvider().discover(Path()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/providers/test_just.py::test_discover_returns_empty_on_timeout -v`
Expected: FAIL — `subprocess.TimeoutExpired` propagates out of `discover` (it is not currently caught).

- [ ] **Step 3: Add the timeout and catch clause**

In `src/nur/providers/just.py`, edit the `discover` method's `subprocess.run` call and its `except` handling so it reads:

```python
    def discover(self, cwd: Path) -> list[Task]:
        try:
            result = subprocess.run(
                ["just", "--dump", "--dump-format", "json"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=5.0,
            )
        except OSError:
            log.warning("nur: 'just' not installed; skipping justfile tasks")
            return []
        except subprocess.TimeoutExpired:
            log.warning("nur: 'just --dump' timed out; skipping justfile tasks")
            return []
        try:
            return parse_dump(result.stdout, self._source_file(cwd))
        except (json.JSONDecodeError, AttributeError) as exc:
            log.warning("nur: skipping justfile (%s)", exc)
            return []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/providers/test_just.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nur/providers/just.py tests/unit/providers/test_just.py
git commit -m "fix(just): time out the just --dump subprocess after 5s"
```

---

### Task 2: Background scan inside `NurApp`

**Files:**
- Modify: `src/nur/tui/app.py` (constructor, `on_mount`, `_rebuild`, `on_worker_state_changed`, `launch`, imports)
- Test: `tests/unit/tui/test_app_navigation.py` (update construction + add scanning/empty tests)
- Test: `tests/integration/tui/test_app_execution.py` (update construction + wait for scan)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `NurApp.__init__(self, cwd: Path, scan: Callable[[], Registry]) -> None`
  - `NurApp._task_registry: Registry | None` — `None` until the scan finishes.
  - `launch(cwd: Path) -> int` — no longer takes a registry. Task 3 (cli.py) calls this.

- [ ] **Step 1: Write the new failing tests**

Add these two tests to `tests/unit/tui/test_app_navigation.py`. They reference the new `scan=` keyword and the `_task_registry` attribute. Also add the `threading` and `time` imports at the top of the file (`import threading`, `import time`).

```python
async def _wait_scanned(app, pilot) -> None:
    # The scan runs on a thread worker; poll a wall-clock deadline until it lands.
    deadline = time.monotonic() + 5.0
    while app._task_registry is None:
        if time.monotonic() >= deadline:
            pytest.fail("scan did not complete within 5 seconds")
        await pilot.pause(0.02)
    await pilot.pause()


@pytest.mark.asyncio
async def test_shows_scanning_then_populates() -> None:
    release = threading.Event()

    def slow_scan() -> Registry:
        release.wait(5.0)
        return _registry()

    app = NurApp(Path(), scan=slow_scan)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._task_registry is None
        assert "scanning" in str(app._status_text).lower()
        release.set()
        await _wait_scanned(app, pilot)
        assert app.selected_task is not None


@pytest.mark.asyncio
async def test_empty_scan_shows_no_tasks_found() -> None:
    app = NurApp(Path(), scan=lambda: Registry([]))
    async with app.run_test() as pilot:
        await _wait_scanned(app, pilot)
        assert app.selected_task is None
        assert "no tasks found" in str(app._detail_text)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/unit/tui/test_app_navigation.py::test_shows_scanning_then_populates tests/unit/tui/test_app_navigation.py::test_empty_scan_shows_no_tasks_found -v`
Expected: FAIL — `NurApp.__init__()` does not accept a `scan` keyword yet (TypeError).

- [ ] **Step 3: Change the imports in `app.py`**

At the top of `src/nur/tui/app.py`, `Registry` is currently imported only under `TYPE_CHECKING`. It is now needed at runtime (for `Registry([])` and `isinstance`). Add a runtime import and a `Callable` type import. The final import block should include:

```python
from nur.execution import ProcessRunner
from nur.registry import Registry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from nur.models import Task
```

(Remove `Registry` from the `TYPE_CHECKING` block since it is now a top-level import.)

- [ ] **Step 4: Rewrite the constructor**

Replace the `__init__` signature and the registry/`selected_task` lines. The new constructor:

```python
    def __init__(self, cwd: Path, scan: Callable[[], Registry]) -> None:
        super().__init__()
        self._scan = scan
        self._cwd = cwd
        self._task_registry: Registry | None = None
        self.selected_task: Task | None = None
        self.exit_code = 0
        self._task_running = False
        self._runner = ProcessRunner()
        self._output_lines: list[str] = []
        self._status_text = ""
        self._detail_text = ""
```

(Keep the surrounding explanatory comments about `_task_running` that already exist.)

- [ ] **Step 5: Rewrite `on_mount` to start the scan worker**

```python
    def on_mount(self) -> None:
        self._set_status("scanning…")
        self._set_detail("scanning for tasks…")
        # Focus the list (not the Input) so '/', 'j', 'k', 'r', 'q' fire as
        # app bindings instead of being typed into the filter box.
        self.query_one("#tasks", ListView).focus()
        # Discovery may spawn subprocesses (e.g. `just --dump`), so run it off
        # the UI thread. Non-exclusive: it must not be cancelled by the
        # exclusive `task-run` worker group.
        self.run_worker(self._scan, thread=True, name="scan")
```

- [ ] **Step 6: Guard `_rebuild` for the three states**

Replace the body of `_rebuild` so a `None` registry means "scanning" and an empty registry means "no tasks found":

```python
    def _rebuild(self, query: str) -> None:
        lv = self.query_one("#tasks", ListView)
        lv.clear()
        if self._task_registry is None:
            self.selected_task = None
            self._set_detail("scanning for tasks…")
            return
        matches = self._matches(query)
        current_prefix = None
        first_index = None  # index of the first selectable TaskItem
        idx = 0
        for task in matches:
            if task.prefix != current_prefix:
                current_prefix = task.prefix
                lv.append(HeaderItem(task.prefix))
                idx += 1
            lv.append(TaskItem(task))
            if first_index is None:
                first_index = idx
            idx += 1
        if matches:
            lv.index = first_index
            self._select_task(matches[0])  # drive detail from data, not widgets
        else:
            self.selected_task = None
            self._set_detail(
                "no tasks found"
                if self._task_registry.is_empty()
                else "no matches"
            )
```

- [ ] **Step 7: Point `_matches` at the (now non-None) registry**

`_matches` is only reached from `_rebuild` after the `None` guard, so it can assume a registry. Update its loop source:

```python
    def _matches(self, query: str) -> list[Task]:
        q = query.lower()
        out = []
        assert self._task_registry is not None  # guarded by _rebuild
        for t in self._task_registry.all():
            hay = f"{t.name} {t.description or ''}".lower()
            if q in hay:
                out.append(t)
        return out
```

- [ ] **Step 8: Handle the scan worker completing**

In `on_worker_state_changed`, add a `"scan"` branch before the existing `"task-run"` branch:

```python
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "scan" and event.worker.state in {
            WorkerState.SUCCESS,
            WorkerState.ERROR,
        }:
            if event.worker.state == WorkerState.SUCCESS and isinstance(
                event.worker.result, Registry
            ):
                self._task_registry = event.worker.result
                self._set_status("")
            else:
                self._task_registry = Registry([])
                self._set_status("scan failed")
            self._rebuild(self.query_one("#filter", Input).value)
            return
        if event.worker.name == "task-run" and event.worker.state in {
            WorkerState.SUCCESS,
            WorkerState.ERROR,
        }:
            success = event.worker.state == WorkerState.SUCCESS
            result: object = event.worker.result if success else 1
            self.exit_code = result if isinstance(result, int) else 1
            self._task_running = False
            self._set_status(f"exited({self.exit_code})")
```

- [ ] **Step 9: Update `launch` to build the scan callable**

Replace the `launch` function at the bottom of `app.py`:

```python
def launch(cwd: Path) -> int:  # pragma: no cover
    from nur.discovery import discover  # noqa: PLC0415

    app = NurApp(cwd, scan=lambda: discover(cwd))
    app.run()  # needs a real terminal; exercised manually, not in CI
    return app.exit_code
```

- [ ] **Step 10: Update existing navigation tests to the new constructor**

In `tests/unit/tui/test_app_navigation.py`, change every `NurApp(_registry(), Path())` to `NurApp(Path(), scan=lambda: _registry())`, and the one `NurApp(registry, Path())` (in `test_detail_includes_task_definition`) to `NurApp(Path(), scan=lambda: registry)`. Then, in each test that asserts on list/selection state, replace the first `await pilot.pause()` with `await _wait_scanned(app, pilot)` so the scan has populated before asserting. For `test_filter_narrows_list` and `test_filter_with_no_matches_clears_selection`, insert `await _wait_scanned(app, pilot)` immediately after entering the `run_test()` block, before pressing `/`. `test_quit_binding` does not need the wait (it only checks the app stops).

- [ ] **Step 11: Update execution tests to the new constructor**

In `tests/integration/tui/test_app_execution.py`, change every `NurApp(_registry(...), Path())` to `NurApp(Path(), scan=lambda: _registry(...))` (capture the argv value in the lambda, e.g. `scan=lambda argv=argv: _registry(argv)`). Add a scan-wait helper mirroring the navigation one, and call it before pressing `r`:

```python
async def _wait_scanned(app, pilot) -> None:
    await _wait_until(
        pilot,
        lambda: app._task_registry is not None and app.selected_task is not None,
        message="scan did not populate within 15 seconds",
    )
```

Insert `await _wait_scanned(app, pilot)` right after the opening `await pilot.pause()` in `test_run_streams_output_and_sets_status` and `test_interrupt_stops_running_task`. In `test_second_run_blocked_while_running`, add `await _wait_scanned(app, pilot)` before setting `app._task_running = True`.

- [ ] **Step 12: Run the full TUI test suite**

Run: `uv run pytest tests/unit/tui/ tests/integration/tui/ -v`
Expected: PASS — new scanning/empty tests pass and all migrated tests stay green.

- [ ] **Step 13: Commit**

```bash
git add src/nur/tui/app.py tests/unit/tui/test_app_navigation.py tests/integration/tui/test_app_execution.py
git commit -m "feat(tui): run task discovery in a background worker"
```

---

### Task 3: Wire the CLI to launch instantly without pre-scanning

**Files:**
- Modify: `src/nur/cli.py:60-77`
- Test: `tests/integration/test_cli.py:39-59`

**Interfaces:**
- Consumes: `launch(cwd: Path) -> int` from Task 2.
- Produces: no new symbols. Behavior: the no-argument path launches the TUI without calling `discover()`; the empty-directory case launches the TUI too (no "no supported task files" message on that path).

- [ ] **Step 1: Update the CLI tests to the new behavior**

In `tests/integration/test_cli.py`:

Replace `test_empty_dir_no_tasks_message` (it asserted the old print-and-exit behavior, which is gone) with a test that an empty directory still launches the TUI:

```python
def test_empty_dir_still_launches_tui(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called = {}

    def fake_launch(cwd) -> int:
        called["launched"] = True
        return 0

    import nur.tui.app

    monkeypatch.setattr(nur.tui.app, "launch", fake_launch)
    assert main([]) == 0
    assert called["launched"] is True
```

Update `test_no_args_launches_tui_and_returns_its_code` so `fake_launch` takes only `cwd` and no longer inspects a registry:

```python
def test_no_args_launches_tui_and_returns_its_code(tmp_path, monkeypatch) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    monkeypatch.chdir(tmp_path)
    called = {}

    def fake_launch(cwd) -> int:
        called["launched"] = True
        return 5

    # main() does `from nur.tui.app import launch` lazily, so patch it there.
    import nur.tui.app

    monkeypatch.setattr(nur.tui.app, "launch", fake_launch)
    assert main([]) == 5
    assert called["launched"] is True
```

- [ ] **Step 2: Run the updated CLI tests to verify they fail**

Run: `uv run pytest tests/integration/test_cli.py::test_empty_dir_still_launches_tui tests/integration/test_cli.py::test_no_args_launches_tui_and_returns_its_code -v`
Expected: FAIL — `main([])` in an empty dir still hits the old message/exit path, and `launch` is still called with `(registry, cwd)`.

- [ ] **Step 3: Reorder the command body so the TUI path skips discovery**

In `src/nur/cli.py`, replace the block from `cwd = Path.cwd()` through the `task is None` branch so discovery is no longer run before the TUI launch. The `_run` body from the `cwd` line onward becomes:

```python
    cwd = Path.cwd()

    if task is None:
        # Launch the TUI immediately; it scans in the background. Do NOT call
        # discover() here — that is what used to block startup.
        from nur.tui.app import launch  # noqa: PLC0415

        raise typer.Exit(launch(cwd))

    registry = discover(cwd)

    if task == "list":
        typer.echo(format_list(registry))
        raise typer.Exit(0)

    try:
        resolved = registry.resolve(task)
    except AmbiguousTaskError as exc:
        typer.echo(
            f"nur: '{exc.name}' is ambiguous; candidates: {', '.join(exc.candidates)}",
            err=True,
        )
        raise typer.Exit(2) from exc
    except UnknownTaskError as exc:
        msg = f"nur: unknown task '{exc.query}'"
        if exc.suggestions:
            msg += f"; did you mean: {', '.join(exc.suggestions)}?"
        typer.echo(msg, err=True)
        raise typer.Exit(2) from exc

    raise typer.Exit(run_direct(resolved, extra, cwd))
```

(The old `if registry.is_empty(): ... "no supported task files found"` block is deleted entirely.)

- [ ] **Step 4: Run the CLI tests to verify they pass**

Run: `uv run pytest tests/integration/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and the project gates**

Run: `uv run pytest`
Expected: PASS (all tests).

Run: `uv run ruff check . && uv run mypy src && uv run basedpyright`
Expected: clean — no lint/type errors introduced.

- [ ] **Step 6: Commit**

```bash
git add src/nur/cli.py tests/integration/test_cli.py
git commit -m "feat(cli): launch the TUI without a synchronous pre-scan"
```

---

## Notes for the implementer

- **Why a thread worker, not async:** `discover()` does blocking file I/O and (for `just`) a subprocess. Textual's `run_worker(..., thread=True)` keeps the UI responsive; the result is read from `event.worker.result` in `on_worker_state_changed`, exactly like the existing `task-run` worker.
- **Filtering during a scan is harmless:** typing in the filter calls `on_input_changed → _rebuild`, which short-circuits on the `None` registry and keeps showing `scanning…`. Once the scan lands, `on_worker_state_changed` calls `_rebuild` with the current filter value, so a filter typed mid-scan is honored.
- **Manual smoke test** (needs a real terminal, not CI): in a directory with a `justfile`, run `nur` and confirm the UI paints immediately with `scanning…`, then the task list fills in. In an empty directory, confirm the UI opens and shows `no tasks found`.
