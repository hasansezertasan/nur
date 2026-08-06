from typing import TYPE_CHECKING

from nur.providers.cargo_make import CargoMakeProvider

if TYPE_CHECKING:
    from pathlib import Path

TOML = """
[config]
skip_core_tasks = true

[tasks.build]
description = "Build the project"
command = "cargo"
args = ["build", "--release"]

[tasks.test]
command = "cargo"
args = ["test"]

[tasks.format]
description = "Format sources"
script = ["cargo fmt", "cargo clippy"]

[tasks.flow]
description = "Composite task with no command"
dependencies = ["build", "test"]

[tasks.internal]
private = true
command = "echo"
args = ["secret"]
"""


def _write(tmp_path: Path, text: str, name: str = "Makefile.toml") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, TOML)
    assert CargoMakeProvider().detect(tmp_path)


def test_detect_false_without_tasks_table(tmp_path) -> None:
    _write(tmp_path, "[config]\nskip_core_tasks = true\n")
    assert not CargoMakeProvider().detect(tmp_path)


def test_detect_false_without_file(tmp_path) -> None:
    assert not CargoMakeProvider().detect(tmp_path)


def test_detect_false_on_empty_tasks_table(tmp_path) -> None:
    _write(tmp_path, "[tasks]\n")
    assert not CargoMakeProvider().detect(tmp_path)


def test_discover_command_and_args(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["build"].argv_base == ("cargo", "make", "build")
    assert tasks["build"].definition == "cargo build --release"
    assert tasks["build"].description == "Build the project"
    assert tasks["build"].source_file == "Makefile.toml"
    assert tasks["build"].prefix == "cargo-make"


def test_description_absent_is_none(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["test"].definition == "cargo test"
    assert tasks["test"].description is None


def test_script_list_is_joined(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["format"].definition == "cargo fmt && cargo clippy"


def test_bare_command_without_args(tmp_path) -> None:
    _write(tmp_path, '[tasks.hello]\ncommand = "echo"\n')
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["hello"].definition == "echo"


def test_multiline_string_script_is_flattened(tmp_path) -> None:
    _write(tmp_path, '[tasks.many]\nscript = """\necho a\necho b\n"""\n')
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["many"].definition == "echo a && echo b"


def test_script_file_table_definition(tmp_path) -> None:
    _write(tmp_path, '[tasks.check]\nscript = { file = "scripts/check.sh" }\n')
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["check"].definition == "file: scripts/check.sh"


def test_script_sections_table_definition(tmp_path) -> None:
    _write(
        tmp_path,
        '[tasks.flow]\nscript = { pre = "echo a", '
        'main = ["echo b", "echo c"], post = "echo d" }\n',
    )
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["flow"].definition == "echo a && echo b && echo c && echo d"


def test_task_without_command_has_empty_definition(tmp_path) -> None:
    _write(tmp_path, TOML)
    tasks = {t.name: t for t in CargoMakeProvider().discover(tmp_path)}
    assert tasks["flow"].definition == ""
    assert tasks["flow"].description == "Composite task with no command"


def test_private_tasks_are_skipped(tmp_path) -> None:
    _write(tmp_path, TOML)
    names = {t.name for t in CargoMakeProvider().discover(tmp_path)}
    assert "internal" not in names


def test_disabled_tasks_are_skipped(tmp_path) -> None:
    _write(tmp_path, '[tasks.old]\ndisabled = true\ncommand = "echo"\n')
    names = {t.name for t in CargoMakeProvider().discover(tmp_path)}
    assert "old" not in names


def test_all_private_detects_but_discovers_nothing(tmp_path) -> None:
    # A table of only-hidden tasks still detects as a cargo-make project but
    # contributes no runnable tasks (mirrors mise's `hide` behavior).
    _write(tmp_path, '[tasks.internal]\nprivate = true\ncommand = "echo"\n')
    provider = CargoMakeProvider()
    assert provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []


def test_discover_empty_without_file(tmp_path) -> None:
    assert CargoMakeProvider().discover(tmp_path) == []


def test_absent_file_emits_no_warning(tmp_path, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="nur"):
        assert not CargoMakeProvider().detect(tmp_path)
        assert CargoMakeProvider().discover(tmp_path) == []
    assert caplog.records == []


def test_malformed_toml_returns_empty(tmp_path) -> None:
    _write(tmp_path, "[tasks.build\ncommand = ")
    assert CargoMakeProvider().discover(tmp_path) == []
