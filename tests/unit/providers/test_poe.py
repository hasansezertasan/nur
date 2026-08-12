from typing import TYPE_CHECKING

from nur.core.providers.poe import PoeProvider

if TYPE_CHECKING:
    from pathlib import Path

TOML = """
[tool.poe.tasks]
test = "pytest"
build = {cmd = "python -m build", help = "build wheel"}
"""


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(text)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, TOML)
    assert PoeProvider().detect(tmp_path)


def test_detect_false_without_poe_table(tmp_path) -> None:
    _write(tmp_path, "[tool.pdm.scripts]\nx = 'y'\n")
    assert not PoeProvider().detect(tmp_path)


def test_discover(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in PoeProvider().discover(tmp_path)}
    assert tasks["test"].argv_base == ("poe", "test")
    assert tasks["test"].definition == "pytest"
    assert tasks["build"].argv_base == ("poe", "build")
    assert tasks["build"].description == "build wheel"
    assert tasks["build"].definition == "python -m build"


def test_coexists_with_pdm(tmp_path) -> None:
    _write(tmp_path, "[tool.pdm.scripts]\nx='a'\n[tool.poe.tasks]\ny='b'\n")
    names = {t.name for t in PoeProvider().discover(tmp_path)}
    assert names == {"y"}


def test_discover_malformed_returns_empty(tmp_path, caplog) -> None:
    _write(tmp_path, "not = = valid")
    assert PoeProvider().discover(tmp_path) == []
    assert any("pyproject.toml" in r.message for r in caplog.records)


def test_task_with_cmd_as_list_is_joined(tmp_path) -> None:
    _write(
        tmp_path, '[tool.poe.tasks]\nserve = {cmd = ["python", "-m", "http.server"]}\n'
    )
    tasks = {t.name: t for t in PoeProvider().discover(tmp_path)}
    assert "http.server" in (tasks["serve"].definition or "")
