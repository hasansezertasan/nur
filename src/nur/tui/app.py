from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, ListItem, ListView, RichLog, Static
from textual.worker import Worker, WorkerState

from nur.core.execution import ProcessRunner
from nur.core.registry import Registry

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from nur.core.models import Task

log = logging.getLogger("nur")

__all__ = ["HeaderItem", "NurApp", "TaskItem", "launch"]


class TaskItem(ListItem):
    def __init__(self, task: Task) -> None:
        super().__init__(Static(f"  {task.name}"))
        self.nur_task = task


class HeaderItem(ListItem):
    def __init__(self, prefix: str) -> None:
        super().__init__(Static(prefix, classes="group-header"))
        self.disabled = True
        self.nur_task = None


class NurApp(App[None]):
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("q", "quit", "quit"),
        ("escape", "quit", "quit"),
        ("slash", "focus_filter", "filter"),
        ("j", "next", "down"),
        ("k", "prev", "up"),
        ("r", "run", "run"),
        # `priority=True` is required for this binding to have any chance of
        # firing: Textual's App reserves ctrl+c for its own internal handling
        # in some versions, and a plain (non-priority) binding loses to that.
        # Whether this actually reaches the terminal as SIGINT-to-app vs.
        # being intercepted by Textual/the OS is version- and platform-
        # dependent, so the interrupt path is also verified directly at the
        # action level (see tests/tui/test_app_execution.py).
        Binding("ctrl+c", "interrupt", "interrupt", priority=True),
        ("question_mark", "toggle_help", "help"),
    ]

    def __init__(self, cwd: Path, scan: Callable[[], Registry]) -> None:
        super().__init__()
        self._scan = scan
        self._cwd = cwd
        self._task_registry: Registry | None = None
        # True once a scan raised, so the empty list reads "scan failed" instead
        # of the misleading "no tasks found".
        self._scan_failed = False
        self.selected_task: Task | None = None
        self.exit_code = 0
        # NOTE: named `_task_running`, not `_running` — `App` already has a
        # private `_running` attribute (whether the app's event loop is
        # live), and shadowing it broke worker bookkeeping.
        self._task_running = False
        self._runner = ProcessRunner()
        # Plain mirrors of the panes, so tests assert on these instead of poking
        # Textual widget internals (whose attribute names churn across versions).
        self._output_lines: list[str] = []
        self._status_text = ""
        self._detail_text = ""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="filter", id="filter")
        with Horizontal():
            yield ListView(id="tasks")
            with Vertical(id="right"):
                yield Static("", id="detail")
                yield RichLog(id="output", highlight=False, markup=False)
                yield Static("", id="status")

    def on_mount(self) -> None:
        self._set_status("scanning…")
        self._set_detail("scanning for tasks…")
        # Focus the list (not the Input) so '/', 'j', 'k', 'r', 'q' fire as
        # app bindings instead of being typed into the filter box.
        self.query_one("#tasks", ListView).focus()
        # Discovery does blocking file I/O across many providers, so run it off
        # the UI thread. It lives in its own "scan" worker group, so the
        # exclusive `task-run` worker (default group) can never cancel it.
        # exit_on_error=False: a scan failure is handled by the ERROR branch
        # in on_worker_state_changed (status "scan failed"), not a crash.
        self.run_worker(
            self._scan, thread=True, name="scan", group="scan", exit_on_error=False
        )

    def _matches(self, query: str) -> list[Task]:
        q = query.lower()
        out = []
        assert self._task_registry is not None  # guarded by _rebuild
        for t in self._task_registry.all():
            hay = f"{t.name} {t.description or ''}".lower()
            if q in hay:
                out.append(t)
        return out

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
            if not self._task_registry.is_empty():
                self._set_detail("no matches")
            elif self._scan_failed:
                self._set_detail("scan failed")
            else:
                self._set_detail("no tasks found")

    def _set_detail(self, text: str) -> None:
        self._detail_text = text
        self.query_one("#detail", Static).update(text)

    def _set_status(self, text: str) -> None:
        self._status_text = text
        self.query_one("#status", Static).update(text)

    def _select_task(self, task: Task) -> None:
        self.selected_task = task
        detail = (
            f"{task.qualified_name}\n"
            f"{task.description or ''}\n\n"
            f"$ {' '.join(task.argv_base)}\n"
            f"from {task.source_file}"
        )
        if task.definition:
            detail += f"\n\n{task.definition}"
        self._set_detail(detail)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        task = getattr(event.item, "nur_task", None)
        if task is not None:
            self._select_task(task)

    def on_list_view_selected(
        self, _event: ListView.Selected
    ) -> None:  # pragma: no cover - Enter spawns a real subprocess run
        # Enter on a task row runs the current selection.
        self.action_run()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._rebuild(event.value)

    def action_focus_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_next(self) -> None:
        self.query_one("#tasks", ListView).action_cursor_down()

    def action_prev(self) -> None:
        self.query_one("#tasks", ListView).action_cursor_up()

    def action_toggle_help(self) -> None:
        self._set_status("keys: j/k move · / filter · r run · q quit")

    def action_run(self) -> None:
        if self._task_running or self.selected_task is None:
            return
        task = self.selected_task
        self._task_running = True
        self._output_lines = []
        self.query_one("#output", RichLog).clear()
        self._set_status(f"running… $ {' '.join(task.argv_base)}")
        self.run_worker(
            lambda: self._runner.run(
                task.run_argv(),
                self._cwd,
                lambda line: self.call_from_thread(self._append_output, line),
            ),
            thread=True,
            exclusive=True,
            name="task-run",
        )

    def _append_output(self, line: str) -> None:
        text = line.rstrip("\n")
        self._output_lines.append(text)
        self.query_one("#output", RichLog).write(text)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name == "scan" and event.worker.state in {
            WorkerState.SUCCESS,
            WorkerState.ERROR,
        }:
            if event.worker.state == WorkerState.SUCCESS and isinstance(
                event.worker.result, Registry
            ):
                self._task_registry = event.worker.result
                self._scan_failed = False
                self._set_status("")
            else:
                # The exception is otherwise lost: `exit_on_error=False` keeps it
                # off stderr, and stderr is hidden behind the TUI anyway.
                if event.worker.state == WorkerState.ERROR:
                    log.error("nur: task discovery failed: %s", event.worker.error)
                self._task_registry = Registry([])
                self._scan_failed = True
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

    def action_interrupt(self) -> None:
        self._runner.interrupt()

    async def action_quit(self) -> None:
        # Don't strand a running child when the user quits mid-run.
        if self._task_running:
            self._runner.interrupt()  # pragma: no cover - requires a live child mid-run
        self.exit()


def launch(cwd: Path) -> int:  # pragma: no cover
    from nur.core.discovery import discover  # ruff: ignore[import-outside-top-level]

    app = NurApp(cwd, scan=lambda: discover(cwd))
    app.run()  # needs a real terminal; exercised manually, not in CI
    return app.exit_code
