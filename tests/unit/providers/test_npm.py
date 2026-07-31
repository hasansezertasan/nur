import json
from typing import TYPE_CHECKING

from nur.providers.npm import NpmProvider, resolve_pm

if TYPE_CHECKING:
    from pathlib import Path


def _write(
    tmp_path: Path, scripts: dict[str, str], lockfile: str | None = None
) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": scripts}))
    if lockfile:
        (tmp_path / lockfile).write_text("")
    return tmp_path


def test_detect_true_when_package_json_present(tmp_path) -> None:
    _write(tmp_path, {})
    assert NpmProvider().detect(tmp_path)


def test_detect_false_when_absent(tmp_path) -> None:
    assert not NpmProvider().detect(tmp_path)


def test_discover_builds_tasks(tmp_path) -> None:
    _write(tmp_path, {"test": "vitest", "build": "tsc"}, lockfile="pnpm-lock.yaml")
    tasks = {t.name: t for t in NpmProvider().discover(tmp_path)}
    assert tasks["test"].argv_base == ("pnpm", "run", "test")
    assert tasks["test"].definition == "vitest"
    assert tasks["test"].source_file == "package.json"


def test_resolve_pm_precedence(tmp_path) -> None:
    for lock in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"]:
        (tmp_path / lock).write_text("")
    assert resolve_pm(tmp_path) == "bun"  # bun wins the precedence order


def test_resolve_pm_recognizes_text_bun_lock(tmp_path) -> None:
    # Modern Bun (>=1.2) writes a text `bun.lock`, not the legacy `bun.lockb`.
    (tmp_path / "bun.lock").write_text("")
    assert resolve_pm(tmp_path) == "bun"


def test_resolve_pm_default_npm(tmp_path) -> None:
    assert resolve_pm(tmp_path) == "npm"


def test_discover_sets_npm_passthrough_separator(tmp_path) -> None:
    _write(tmp_path, {"test": "vitest"})  # no lockfile -> npm
    task = NpmProvider().discover(tmp_path)[0]
    # npm requires the `--` separator to forward args to the script.
    assert task.run_argv(["--watch"]) == ["npm", "run", "test", "--", "--watch"]


def test_discover_no_scripts_key_returns_empty(tmp_path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"name": "x"}))
    assert NpmProvider().discover(tmp_path) == []


def test_discover_malformed_json_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "package.json").write_text("{ not json")
    assert NpmProvider().discover(tmp_path) == []
    assert any("package.json" in r.message for r in caplog.records)


def test_scripts_not_an_object_is_skipped(tmp_path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": "not-an-object"}')
    assert NpmProvider().discover(tmp_path) == []
