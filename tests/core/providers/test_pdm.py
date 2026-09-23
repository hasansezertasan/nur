from typing import TYPE_CHECKING

from nur.core.providers.pdm import PdmProvider

if TYPE_CHECKING:
    from pathlib import Path

TOML = """
[tool.pdm.scripts]
test = "pytest"
lint = {cmd = "ruff check", help = "run linter"}
serve = {shell = "python -m http.server"}
_.env = {FOO = "bar"}
"""


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(text)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, TOML)
    assert PdmProvider().detect(tmp_path)


def test_detect_false_without_pdm_table(tmp_path) -> None:
    _write(tmp_path, "[tool.poe.tasks]\nx = 'y'\n")
    assert not PdmProvider().detect(tmp_path)


def test_detect_false_without_pyproject(tmp_path) -> None:
    assert not PdmProvider().detect(tmp_path)


def test_discover(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in PdmProvider().discover(tmp_path)}
    assert "_" not in tasks
    assert tasks["test"].argv_base == ("pdm", "run", "test")
    assert tasks["test"].definition == "pytest"
    assert tasks["lint"].description == "run linter"
    assert tasks["lint"].definition == "ruff check"
    assert tasks["serve"].definition == "python -m http.server"


def test_discover_malformed_returns_empty(tmp_path, caplog) -> None:
    _write(tmp_path, "not = = valid")
    assert PdmProvider().discover(tmp_path) == []
    assert any("pyproject.toml" in r.message for r in caplog.records)


def test_script_with_cmd_as_list_is_joined(tmp_path) -> None:
    _write(
        tmp_path,
        '[tool.pdm.scripts]\nserve = {cmd = ["python", "-m", "http.server"]}\n',
    )
    tasks = {t.name: t for t in PdmProvider().discover(tmp_path)}
    assert tasks["serve"].argv_base == ("pdm", "run", "serve")
    assert "http.server" in (tasks["serve"].definition or "")
