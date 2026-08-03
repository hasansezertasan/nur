# TUI Background Scan — Design

**Date:** 2026-08-03
**Status:** Approved

## Problem

Launching the TUI (`nur` with no arguments) blocks on task discovery before
anything appears on screen. `cli.py` calls `discover(cwd)` synchronously, builds
a complete `Registry`, and only then calls `launch(registry, cwd)`. The user
stares at a blank terminal until discovery finishes.

Discovery is fast for most providers (they only read files), but the `just`
provider spawns `just --dump` as a subprocess (`just.py:44`) with **no timeout**.
A slow or hung `just` blocks startup indefinitely.

**Goal:** the TUI appears immediately; discovery runs in the background and
populates the task list when it completes.

## Decisions

- **Population granularity: atomic on completion.** The list stays empty (with a
  `scanning…` indicator) until the entire scan finishes, then all tasks appear at
  once. Simpler than per-provider incremental population; the indicator carries
  the feedback.
- **Empty case: always launch the TUI.** The current CLI behavior of printing
  `no supported task files found` and exiting *without* launching is dropped for
  the no-argument path. The TUI always opens, scans in the background, and shows
  `no tasks found` in the list if the scan comes back empty. The user presses `q`
  to exit. This is a deliberate behavior change.
- **Constructor seam: inject a scan callable.** `NurApp` takes a
  `scan: Callable[[], Registry]` rather than a pre-built `Registry`. The app owns
  the asynchronous scan; the callable keeps the TUI decoupled from provider
  internals and makes the scanning state trivially testable.

## Architecture

Split "open the TUI" from "discover tasks."

- **`cli.py`** — the `task is None` branch no longer calls `discover()` and no
  longer checks `registry.is_empty()`. It becomes:

  ```python
  if task is None:
      from nur.tui.app import launch  # noqa: PLC0415
      raise typer.Exit(launch(cwd))
  ```

  The `nur list` and direct-run (`nur <task>`) paths keep discovering
  synchronously — unchanged.

- **`launch(cwd)`** — signature drops the `registry` parameter. Builds
  `NurApp(cwd, scan=lambda: discover(cwd))` and runs it.

## Components

### `NurApp` (`src/nur/tui/app.py`)

**Constructor:**

```python
def __init__(self, cwd: Path, scan: Callable[[], Registry]) -> None:
```

- `self._scan = scan`
- `self._cwd = cwd`
- `self._task_registry: Registry | None = None`  — starts empty, populated by the
  background scan.

**Lifecycle:**

- `on_mount`: render the empty list, set status `scanning…`, then start a
  **non-exclusive thread worker** named `"scan"` that calls `self._scan()`.
  Non-exclusive so it does not collide with the exclusive `task-run` worker
  group (the scan is long finished before any task runs, but the groups stay
  independent regardless).
- `on_worker_state_changed`: add a `"scan"` branch alongside the existing
  `"task-run"` branch.
  - On `SUCCESS`: `self._task_registry = event.worker.result`, clear the
    scanning status, call `_rebuild(<current filter value>)`.
  - On `ERROR`: log the worker's exception, set status `scan failed`, set the
    `_scan_failed` flag, and store an empty registry so the app leaves the
    `scanning…` state. (The worker is started with `exit_on_error=False` so the
    exception reaches this branch instead of crashing the app.)
- `_matches` / `_rebuild`: guard against a `None` registry (return no matches).

### List states

| State | Status bar | Detail pane | List |
| --- | --- | --- | --- |
| Scanning | `scanning…` | `scanning for tasks…` | empty |
| Done, tasks found | (normal) | task detail (current behavior) | populated |
| Done, empty | (cleared) | `no tasks found` | empty |
| Scan failed | `scan failed` | `scan failed` | empty |

Note: `_rebuild` must distinguish "scanning" (registry is `None`) from "empty
result" (registry present, no tasks) from "scan failed" (registry empty and the
`_scan_failed` flag set) from "no matches for filter" (registry has tasks, none
match the query). The failed and empty cases both hold an empty registry, so the
`_scan_failed` flag is what tells them apart.

### `just` provider timeout (`src/nur/providers/just.py`)

Add a **5-second timeout** to the `just --dump` subprocess and catch
`subprocess.TimeoutExpired` (log a warning, return `[]`, same as the existing
`OSError` path). Without this, a hung `just` leaves `scanning…` on screen
forever now that the scan is backgrounded.

## Testing

- **Existing tests** (`tests/unit/tui/test_app_navigation.py`,
  `tests/integration/tui/test_app_execution.py`): update construction from
  `NurApp(registry, Path())` to `NurApp(Path(), scan=lambda: registry)` and
  await the `"scan"` worker before asserting on list contents.
- **New tests:**
  - Scanning state is visible while a blocking scan is in flight (inject a scan
    that waits on an event; assert status `scanning…` and empty list), then
    populates after the event is released.
  - Empty scan result shows `no tasks found`.
  - `just` provider: a scan exceeding the timeout is caught and yields `[]`
    (unit test on the provider, not the TUI).

## Out of scope (YAGNI)

- Per-provider incremental population.
- A manual re-scan key binding.
- A fast `detect()` pre-check gate in the CLI (superseded by "always launch").
