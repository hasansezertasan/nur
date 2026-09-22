import sys

from nur.core.providers.vscode import VsCodeProvider


def _write_tasks(tmp_path, contents: str) -> None:
    vscode_directory = tmp_path / ".vscode"
    vscode_directory.mkdir()
    (vscode_directory / "tasks.json").write_text(contents)


def test_discovers_jsonc_shell_process_and_untyped_tasks(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{
  // VS Code permits comments and trailing commas.
  "version": "2.0.0",
  "tasks": [
    {
      "label": "test", "type": "shell", "command": "pytest", "args": ["-q"],
      "detail": "Run tests"
    },
    {
      "label": "build", "type": "process", "command": "python",
      "args": [{"value": "build.py", "quoting": "strong"}]
    },
    {"label": "lint", "command": "ruff", "args": ["check", "."],},
  ],
}""",
    )

    provider = VsCodeProvider()
    assert provider.detect(tmp_path)
    tasks = {task.name: task for task in provider.discover(tmp_path)}
    assert tasks["test"].argv_base == ("pytest", "-q")
    assert tasks["test"].description == "Run tests"
    assert tasks["build"].definition == "python build.py"
    assert tasks["lint"].source_file == ".vscode/tasks.json"


def test_skips_unsupported_and_non_runnable_tasks(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [
  {"label": "npm", "type": "npm", "script": "test"},
  {"label": "compound", "dependsOn": "test"},
  {"label": "invalid", "type": "shell", "command": 42},
  {"label": "valid", "command": "echo", "args": ["${workspaceFolder}"]}
]}""",
    )
    tasks = VsCodeProvider().discover(tmp_path)
    assert [(task.name, task.argv_base) for task in tasks] == [
        ("valid", ("echo", "${workspaceFolder}"))
    ]


def test_resolves_current_platform_command_and_args(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [{
  "label": "platform", "command": {"osx": "mac-command", "linux": "linux-command"},
  "args": {"osx": ["mac-argument"], "linux": ["linux-argument"]}
}]}""",
    )
    task = VsCodeProvider().discover(tmp_path)[0]
    expected = (
        ("mac-command", "mac-argument")
        if sys.platform == "darwin"
        else ("linux-command", "linux-argument")
    )
    assert task.argv_base == expected


def test_duplicate_labels_use_the_last_task(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [
  {"label": "test", "command": "pytest"},
  {"label": "test", "command": "python", "args": ["-m", "pytest"]}
]}""",
    )
    tasks = VsCodeProvider().discover(tmp_path)
    assert [(task.name, task.argv_base) for task in tasks] == [
        ("test", ("python", "-m", "pytest"))
    ]


def test_ignores_legacy_and_malformed_documents(tmp_path, caplog) -> None:
    _write_tasks(tmp_path, '{"version": "0.1.0", "tasks": []}')
    provider = VsCodeProvider()
    assert not provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []

    (tmp_path / ".vscode" / "tasks.json").write_text('{"version":')
    assert not provider.detect(tmp_path)
    assert any(".vscode/tasks.json" in record.message for record in caplog.records)
