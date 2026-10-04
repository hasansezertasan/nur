from typing import TYPE_CHECKING

import pytest

from nur.core.discovery import discover
from nur.core.providers.invoke import InvokeProvider, parse_tasks

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        ("from invoke import task", "task"),
        ("from invoke import task as t", "t()"),
        ("import invoke", "invoke.task"),
        ("import invoke as inv", "inv.task(pre=[])"),
        ("from invoke.tasks import task", "task"),
    ],
)
def test_decorators(imports: str, decorator: str) -> None:
    tasks = parse_tasks(
        f"{imports}\n@{decorator}\ndef build_all(c):\n"
        '    """Build everything.\n\n    More details.\n    """\n'
    )
    assert len(tasks) == 1
    task = tasks[0]
    assert task.qualified_name == "invoke:build-all"
    assert task.argv_base == ("invoke", "build-all")
    assert task.run_argv(["--clean"]) == ["invoke", "build-all", "--clean"]
    assert task.description == "Build everything."
    assert task.definition == ""
    assert task.source_file == "tasks.py"
    assert not task.run_in_shell


def test_names_and_aliases() -> None:
    tasks = parse_tasks(
        "from invoke import task\n"
        '@task(name="release_all", aliases=("ship", "push_all"))\n'
        "def publish(c): ...\n"
    )
    assert [task.name for task in tasks] == ["release-all", "ship", "push-all"]
    assert all(task.argv_base == ("invoke", task.name) for task in tasks)
    assert all(task.description is None for task in tasks)


@pytest.mark.parametrize(
    "options",
    ["name=compute()", "name=123", "aliases=ALIASES", "**options", "klass=CustomTask"],
)
def test_dynamic_metadata_is_skipped(options: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({options})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "@task\ndef build(c): ...\n",
        "from other import task\n@task\ndef build(c): ...\n",
        "from .invoke import task\n@task\ndef build(c): ...\n",
        "from invoke import task\ntask = other\n@task\ndef build(c): ...\n",
        "from invoke import task\nif True:\n @task\n def build(c): ...\n",
        "from invoke import task\ndef helper():\n @task\n def build(c): ...\n",
        "from invoke import task\n@wrapper\n@task\ndef build(c): ...\n",
        "from invoke import task\n@task\ndef build(c): ...\nbuild = None\n",
        "from invoke import task\n@task\ndef build(c): ...\ndel build\n",
    ],
)
def test_unsupported_and_rebound_tasks(text: str) -> None:
    assert parse_tasks(text) == []


def test_detection_and_discovery_never_execute(tmp_path: Path) -> None:
    provider = InvokeProvider()
    assert not provider.detect(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "tasks.py").write_text(
        f'from invoke import task\nopen({str(marker)!r}, "w").write("bad")\n'
        '@task\ndef build(c):\n raise RuntimeError("do not run")\n',
        encoding="utf-8",
    )
    assert provider.detect(tmp_path)
    assert discover(tmp_path).resolve("invoke:build").argv_base == ("invoke", "build")
    assert not marker.exists()


@pytest.mark.parametrize(
    "contents", [b"def broken(", b"\xff", b"from invoke import task\nreturn\n"]
)
def test_invalid_files_are_skipped(tmp_path: Path, caplog, contents: bytes) -> None:
    (tmp_path / "tasks.py").write_bytes(contents)
    assert InvokeProvider().discover(tmp_path) == []
    assert "skipping tasks.py" in caplog.text


def test_missing_file_is_skipped(tmp_path: Path, caplog) -> None:
    assert InvokeProvider().discover(tmp_path) == []
    assert "skipping tasks.py" in caplog.text
