import sys
import time

import pytest

from nur.execution import RUNNER_NOT_FOUND, ProcessRunner, run_direct
from nur.models import Task


def test_run_direct_returns_exit_code(tmp_path) -> None:
    argv = (sys.executable, "-c", "raise SystemExit(3)")
    t = Task(name="x", prefix="py", argv_base=argv)
    assert run_direct(t, [], tmp_path) == 3


def test_run_direct_appends_extra_args(tmp_path) -> None:
    code = "import sys; raise SystemExit(len(sys.argv) - 1)"
    t = Task(name="x", prefix="py", argv_base=(sys.executable, "-c", code))
    assert run_direct(t, ["a", "b"], tmp_path) == 2


def test_process_runner_streams_lines_and_returns_code(tmp_path) -> None:
    lines: list[str] = []
    runner = ProcessRunner()
    argv = [sys.executable, "-c", "print('hello'); print('world')"]
    code = runner.run(argv, tmp_path, on_line=lines.append)
    assert code == 0
    assert [ln.rstrip("\n") for ln in lines] == ["hello", "world"]


def test_process_runner_captures_stderr(tmp_path) -> None:
    lines: list[str] = []
    runner = ProcessRunner()
    argv = [sys.executable, "-c", "import sys; print('err', file=sys.stderr)"]
    runner.run(argv, tmp_path, on_line=lines.append)
    assert any("err" in ln for ln in lines)


def test_run_direct_missing_runner_returns_127(tmp_path, capsys) -> None:
    argv = ("nur-nonexistent-runner-xyz", "run", "x")
    t = Task(name="x", prefix="npm", argv_base=argv)
    assert run_direct(t, [], tmp_path) == RUNNER_NOT_FOUND
    err = capsys.readouterr().err
    assert "nur-nonexistent-runner-xyz" in err
    assert "not installed" in err


def test_process_runner_cleans_up_when_callback_raises(tmp_path) -> None:
    runner = ProcessRunner()
    argv = [sys.executable, "-c", "print('one')"]

    def boom(_line: str) -> None:
        msg = "callback failed"
        raise RuntimeError(msg)

    # The callback error must propagate (not be swallowed) …
    with pytest.raises(RuntimeError, match="callback failed"):
        runner.run(argv, tmp_path, on_line=boom)
    # … and the finally block must still have cleared runner state.
    assert runner._proc is None


def test_process_runner_interrupt_stops_running_child(tmp_path) -> None:
    runner = ProcessRunner()
    argv = [sys.executable, "-c", "import time; time.sleep(30)"]
    result: dict[str, int] = {}

    def target() -> None:
        result["code"] = runner.run(argv, tmp_path, on_line=lambda _l: None)

    import threading

    worker = threading.Thread(target=target)
    worker.start()
    for _ in range(100):  # wait for the child to actually start
        if runner._proc is not None:
            break
        time.sleep(0.02)
    runner.interrupt()
    worker.join(timeout=10)
    assert not worker.is_alive()  # interrupt actually stopped the sleep(30)
    assert result["code"] != 0


def test_process_runner_missing_runner_reports_via_callback(tmp_path) -> None:
    runner = ProcessRunner()
    lines: list[str] = []
    code = runner.run(["nur-nonexistent-runner-xyz", "x"], tmp_path, lines.append)
    assert code == RUNNER_NOT_FOUND
    assert any("not installed" in line for line in lines)
