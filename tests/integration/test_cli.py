import json
from pathlib import Path
from typing import Never

import nur
from nur.cli import format_list, main, split_passthrough
from nur.discovery import discover


def test_split_passthrough() -> None:
    assert split_passthrough(["test", "--", "-x", "-y"]) == (["test"], ["-x", "-y"])
    assert split_passthrough(["test"]) == (["test"], [])


def test_version(capsys) -> None:
    assert main(["--version"]) == 0
    assert nur.__version__ in capsys.readouterr().out


def test_help_returns_zero_not_systemexit(capsys) -> None:
    # -h/--help must return an int (argparse's SystemExit is caught), so main()
    # stays usable from tests/embedders.
    assert main(["-h"]) == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_missing_runner_returns_127(tmp_path, monkeypatch) -> None:
    # A discovered task whose runner binary is absent yields a controlled
    # nonzero status, not a traceback.
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
    monkeypatch.chdir(tmp_path)

    def fake_run(argv, **kwargs) -> Never:
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr("nur.execution.subprocess.run", fake_run)
    assert main(["npm:build"]) == 127


def test_empty_dir_still_launches_tui(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    called = {}

    def fake_launch(cwd) -> int:
        called["launched"] = True
        called["cwd"] = cwd
        return 0

    import nur.tui.app

    monkeypatch.setattr(nur.tui.app, "launch", fake_launch)
    assert main([]) == 0
    assert called["launched"] is True
    assert Path(called["cwd"]).resolve() == tmp_path.resolve()


def test_no_args_launches_tui_and_returns_its_code(tmp_path, monkeypatch) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    monkeypatch.chdir(tmp_path)
    called = {}

    def fake_launch(cwd) -> int:
        called["launched"] = True
        called["cwd"] = cwd
        return 5

    # main() does `from nur.tui.app import launch` lazily, so patch it there.
    import nur.tui.app

    monkeypatch.setattr(nur.tui.app, "launch", fake_launch)
    assert main([]) == 5
    assert called["launched"] is True
    assert Path(called["cwd"]).resolve() == tmp_path.resolve()


def test_list_output(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    monkeypatch.chdir(tmp_path)
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "npm" in out
    assert "test" in out


def test_run_propagates_exit_code(tmp_path, monkeypatch) -> None:
    (tmp_path / "Makefile").write_text("boom:\n\texit 4\n")
    monkeypatch.chdir(tmp_path)
    # make is required for this test; skip if unavailable
    import shutil

    if shutil.which("make") is None:
        import pytest

        pytest.skip("make not installed")
    # GNU Make exits with status 2 on any recipe failure, regardless of the recipe's
    # own exit code. This test verifies nur faithfully returns make's exit code (2)
    # rather than swallowing the failure. Precise exit-code propagation for arbitrary
    # codes is already covered by the run_direct unit tests in tests/test_execution.py.
    assert main(["make:boom"]) == 2


def test_unknown_task_returns_2(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    monkeypatch.chdir(tmp_path)
    assert main(["nope"]) == 2
    assert "unknown" in capsys.readouterr().err.lower()


def test_ambiguous_task_returns_2(tmp_path, monkeypatch, capsys) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "x"}}))
    (tmp_path / "pyproject.toml").write_text("[tool.pdm.scripts]\ntest='y'\n")
    monkeypatch.chdir(tmp_path)
    assert main(["test"]) == 2
    err = capsys.readouterr().err.lower()
    assert "npm:test" in err
    assert "pdm:test" in err


def test_format_list_groups(tmp_path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"a": "x"}}))
    text = format_list(discover(tmp_path))
    assert "npm" in text
    assert "a" in text


def test_unknown_task_offers_suggestion(tmp_path, monkeypatch, capsys):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "x"}}))
    monkeypatch.chdir(tmp_path)
    assert main(["buld"]) == 2
    err = capsys.readouterr().err.lower()
    assert "did you mean" in err
    assert "build" in err
