import json
import shutil

import pytest

from nur.cli import main

requires_make = pytest.mark.skipif(
    shutil.which("make") is None, reason="make not installed"
)
requires_npm = pytest.mark.skipif(
    shutil.which("npm") is None, reason="npm not installed"
)


@requires_make
def test_make_task_runs_and_writes_sentinel(tmp_path, monkeypatch) -> None:
    sentinel = tmp_path / "done.txt"
    (tmp_path / "Makefile").write_text(f"touch:\n\ttouch {sentinel.name}\n")
    monkeypatch.chdir(tmp_path)
    assert main(["make:touch"]) == 0
    assert sentinel.exists()


@requires_make
def test_make_exit_code_propagates(tmp_path, monkeypatch) -> None:
    (tmp_path / "Makefile").write_text("fail:\n\texit 7\n")
    monkeypatch.chdir(tmp_path)
    # GNU Make exits 2 on recipe failure regardless of the recipe's exit code;
    # this verifies nur faithfully returns make's exit code. Precise
    # arbitrary-code propagation is covered by tests/test_execution.py.
    assert main(["make:fail"]) == 2


@requires_make
def test_passthrough_args_reach_runner(tmp_path, monkeypatch) -> None:
    # `make VAR=1 target` — passthrough args are appended to argv.
    out = tmp_path / "arg.txt"
    (tmp_path / "Makefile").write_text(f"show:\n\techo $(WHO) > {out.name}\n")
    monkeypatch.chdir(tmp_path)
    assert main(["make:show", "--", "WHO=nur"]) == 0
    assert out.read_text().strip() == "nur"


@requires_npm
def test_npm_list_discovers_scripts(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"hello": "echo hi"}})
    )
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 0
    assert "hello" in capsys.readouterr().out
