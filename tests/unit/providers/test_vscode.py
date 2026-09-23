import os
import sys
from pathlib import Path

from nur.core.providers.vscode import VsCodeProvider
from nur.core.shell import quote


def _write_tasks(tmp_path, contents: str) -> None:
    vscode_directory = tmp_path / ".vscode"
    vscode_directory.mkdir()
    (vscode_directory / "tasks.json").write_text(contents)


def test_discovers_jsonc_shell_process_and_untyped_tasks(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{
  // VS Code permits comments and trailing commas.
  /* block
     comment */
  "version": "2.0.0",
  "tasks": [
    {
      "label": "test", "type": "shell", "command": "pytest", "args": ["-q"],
      "detail": "Run \\"tests\\" // not a comment"
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
    assert tasks["test"].description == 'Run "tests" // not a comment'
    assert tasks["test"].run_in_shell
    assert tasks["build"].definition == "python build.py"
    assert not tasks["build"].run_in_shell
    assert tasks["lint"].source_file == ".vscode/tasks.json"


def test_skips_unsupported_and_non_runnable_tasks(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [
  {"label": "npm", "type": "npm", "script": "test"},
  {"label": "compound", "dependsOn": "test"},
  {"label": "dependent", "command": "build", "dependsOn": "test"},
  {"label": "hidden", "command": "secret", "hide": true},
  {"label": "invalid", "type": "shell", "command": 42},
  {"label": "editor-variable", "command": "echo", "args": ["${file}"]},
  {"label": "env", "command": "env", "options": {"env": {"A": "1"}}},
  {"label": "custom-shell", "command": "ls", "options": {"shell": {}}},
  {"label": "subdirectory", "command": "ls", "options": {"cwd": "sub"}},
  {"label": "bad-cwd", "command": "ls", "options": {"cwd": 1}},
  {"label": "bad-options", "command": "ls", "options": []},
  {"label": "bad-args", "command": "ls", "args": "-l"},
  "not-a-task",
  {"label": "valid", "command": "echo", "args": ["${workspaceFolder}"],
   "options": {"cwd": "${workspaceFolder}"}}
]} // trailing comment without newline""",
    )
    tasks = VsCodeProvider().discover(tmp_path)
    assert [(task.name, task.argv_base) for task in tasks] == [
        ("valid", ("echo", str(tmp_path.resolve())))
    ]


def test_resolves_static_variables(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NUR_VSCODE_TEST", "value")
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [{
  "label": "variables", "command": "${workspaceRoot}${/}run",
  "args": ["${workspaceFolderBasename}", "${pathSeparator}",
           "${env:NUR_VSCODE_TEST}", "${env:NUR_VSCODE_UNSET_XYZ}"],
  "options": {}
}]}""",
    )
    task = VsCodeProvider().discover(tmp_path)[0]
    assert task.argv_base == (
        f"{tmp_path.resolve()}{os.sep}run",
        tmp_path.resolve().name,
        os.sep,
        "value",
        "",
    )


def test_unsupported_global_options_skip_every_task(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "options": {"env": {"A": "1"}},
  "tasks": [{"label": "test", "command": "pytest"}]}""",
    )
    provider = VsCodeProvider()
    assert provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []


def test_resolves_current_platform_overrides(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [{
  "label": "platform", "type": "process",
  "command": "base-command", "args": ["base-argument"],
  "windows": {"type": "shell", "command": "windows-command",
              "args": ["windows-argument"]},
  "linux": {"type": "shell", "command": "linux-command", "args": ["linux-argument"]},
  "osx": {"type": "shell", "command": "mac-command", "args": ["mac-argument"]}
}]}""",
    )
    task = VsCodeProvider().discover(tmp_path)[0]
    assert task.run_in_shell
    expected = (
        ("mac-command", "mac-argument")
        if sys.platform == "darwin"
        else ("windows-command", "windows-argument")
        if sys.platform == "win32"
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

    (tmp_path / ".vscode" / "tasks.json").write_text(
        '{"version": "2.0.0", "tasks": []} /*'
    )
    assert provider.discover(tmp_path) == []


def test_object_form_command_is_quoted_for_the_shell(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "tasks": [
  {"label": "tool", "type": "shell",
   "command": {"value": "${workspaceFolder}/my tool", "quoting": "strong"},
   "args": ["x"]},
  {"label": "builtin", "type": "shell", "command": {"value": "echo"}},
  {"label": "process", "type": "process", "command": {"value": "my tool"}}
]}""",
    )
    tasks = {task.name: task for task in VsCodeProvider().discover(tmp_path)}
    assert tasks["tool"].argv_base == (quote(f"{tmp_path.resolve()}/my tool"), "x")
    assert tasks["tool"].run_in_shell
    assert tasks["builtin"].argv_base == ("echo",)
    assert tasks["builtin"].run_in_shell
    assert tasks["process"].argv_base == ("my tool",)
    assert not tasks["process"].run_in_shell


def test_block_comments_do_not_fuse_adjacent_tokens(tmp_path, caplog) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "junk": 1/*comment*/2,
  "tasks": [{"label": "test", "command": "pytest"}]}""",
    )
    assert VsCodeProvider().discover(tmp_path) == []
    assert any(".vscode/tasks.json" in record.message for record in caplog.records)


def test_document_platform_options_apply_to_every_task(tmp_path) -> None:
    platform = {"darwin": "osx", "win32": "windows"}.get(sys.platform, "linux")
    _write_tasks(
        tmp_path,
        f"""{{"version": "2.0.0", "{platform}": {{"options": {{"cwd": "sub"}}}},
  "tasks": [{{"label": "test", "command": "pytest"}}]}}""",
    )
    assert VsCodeProvider().discover(tmp_path) == []


def test_tasks_inherit_document_level_defaults(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "type": "process", "command": "python",
  "args": ["-m", "pytest"],
  "tasks": [
    {"label": "inherited"},
    {"label": "own-command", "command": "ruff", "args": ["check"]},
    {"label": "own-type", "type": "shell", "command": "echo hi", "args": []}
  ]}""",
    )
    tasks = {task.name: task for task in VsCodeProvider().discover(tmp_path)}
    assert tasks["inherited"].argv_base == ("python", "-m", "pytest")
    assert not tasks["inherited"].run_in_shell
    assert tasks["own-command"].argv_base == ("ruff", "check")
    assert not tasks["own-command"].run_in_shell
    assert tasks["own-type"].argv_base == ("echo hi",)
    assert tasks["own-type"].run_in_shell


def test_accepts_a_utf8_bom(tmp_path) -> None:
    _write_tasks(tmp_path, "")
    (tmp_path / ".vscode" / "tasks.json").write_bytes(
        b'\xef\xbb\xbf{"version": "2.0.0", "tasks": [{"label": "t", "command": "x"}]}'
    )
    assert [task.name for task in VsCodeProvider().discover(tmp_path)] == ["t"]


def test_task_options_override_document_options(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "options": {"cwd": "sub"}, "tasks": [
  {"label": "root", "command": "ls", "options": {"cwd": "${workspaceFolder}"}},
  {"label": "inherited", "command": "ls"},
  {"label": "malformed", "command": "ls", "options": []}
]}""",
    )
    assert [task.name for task in VsCodeProvider().discover(tmp_path)] == ["root"]


def test_document_env_still_applies_under_task_options(tmp_path) -> None:
    _write_tasks(
        tmp_path,
        """{"version": "2.0.0", "options": {"env": {"A": "1"}}, "tasks": [
  {"label": "test", "command": "pytest", "options": {"cwd": "${workspaceFolder}"}}
]}""",
    )
    assert VsCodeProvider().discover(tmp_path) == []


def test_relative_workspace_resolves_to_an_absolute_path(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_tasks(
        project,
        """{"version": "2.0.0", "tasks": [{
  "label": "build", "command": "${workspaceFolder}/build",
  "args": ["${workspaceFolderBasename}"], "options": {"cwd": "${workspaceFolder}"}
}]}""",
    )
    monkeypatch.chdir(tmp_path)
    task = VsCodeProvider().discover(Path("project"))[0]
    assert task.argv_base == (f"{project.resolve()}/build", "project")
