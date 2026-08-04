from typing import TYPE_CHECKING

from nur.providers.mise import MiseProvider

if TYPE_CHECKING:
    from pathlib import Path

TOML = """
[tools]
python = "3.14"

[tasks.install]
description = "Install dependencies"
alias = "i"
run = "uv sync"

[tasks.test]
run = "pytest"

[tasks.release]
run = ["uv build", "uv publish"]
"""


def _write(tmp_path: Path, text: str, name: str = "mise.toml") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, TOML)
    assert MiseProvider().detect(tmp_path)


def test_detect_false_without_tasks_table(tmp_path) -> None:
    _write(tmp_path, '[tools]\npython = "3.14"\n')
    assert not MiseProvider().detect(tmp_path)


def test_detect_false_without_file(tmp_path) -> None:
    assert not MiseProvider().detect(tmp_path)


def test_detect_false_on_empty_tasks_table(tmp_path) -> None:
    _write(tmp_path, "[tasks]\n")
    assert not MiseProvider().detect(tmp_path)


def test_discover(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["install"].argv_base == ("mise", "run", "install")
    assert tasks["install"].definition == "uv sync"
    assert tasks["install"].description == "Install dependencies"
    assert tasks["install"].source_file == "mise.toml"
    # Tasks without a description surface as None.
    assert tasks["test"].description is None


def test_run_list_is_joined(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["release"].definition == "uv build && uv publish"


def test_string_shorthand_task(tmp_path) -> None:
    _write(tmp_path, '[tasks]\nlint = "ruff check"\n')
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["lint"].definition == "ruff check"
    assert tasks["lint"].description is None


def test_list_shorthand_task_is_joined(tmp_path) -> None:
    _write(tmp_path, '[tasks]\nrelease = ["uv build", "uv publish"]\n')
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["release"].definition == "uv build && uv publish"
    assert tasks["release"].description is None


def test_hidden_task_is_skipped(tmp_path) -> None:
    _write(
        tmp_path,
        '[tasks.secret]\nhide = true\nrun = "deploy"\n\n'
        '[tasks.build]\nrun = "uv build"\n',
    )
    names = {t.name for t in MiseProvider().discover(tmp_path)}
    assert names == {"build"}


def test_dotted_config_file(tmp_path) -> None:
    _write(tmp_path, TOML, name=".mise.toml")
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["install"].source_file == ".mise.toml"


def test_nested_config_file(tmp_path) -> None:
    _write(tmp_path, TOML, name=".config/mise.toml")
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["install"].source_file == ".config/mise.toml"


def test_config_priority_prefers_local(tmp_path) -> None:
    _write(tmp_path, '[tasks]\nx = "from-mise"\n', name="mise.toml")
    _write(tmp_path, '[tasks]\nx = "from-local"\n', name="mise.local.toml")
    tasks = {t.name: t for t in MiseProvider().discover(tmp_path)}
    assert tasks["x"].definition == "from-local"
    assert tasks["x"].source_file == "mise.local.toml"


def test_discover_malformed_returns_empty(tmp_path, caplog) -> None:
    _write(tmp_path, "not = = valid")
    assert MiseProvider().discover(tmp_path) == []
    assert any("mise.toml" in r.message for r in caplog.records)


def test_discover_non_utf8_returns_empty(tmp_path, caplog) -> None:
    # 0xff is never a valid UTF-8 byte, so read_text raises UnicodeDecodeError.
    (tmp_path / "mise.toml").write_bytes(b'[tasks]\nx = "\xff"\n')
    assert MiseProvider().discover(tmp_path) == []
    assert any("mise.toml" in r.message for r in caplog.records)
