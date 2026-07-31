from nur.providers.task import TaskfileProvider, parse_taskfile

TASKFILE = """
version: '3'
tasks:
  build:
    desc: Build it
    cmds:
      - echo build
  test:
    desc: ""
  hidden:
    internal: true
    desc: nope
  short: echo hi
"""


def test_parse_taskfile() -> None:
    tasks = {t.name: t for t in parse_taskfile(TASKFILE)}
    assert tasks["build"].argv_base == ("task", "build")
    assert tasks["build"].description == "Build it"
    assert tasks["build"].prefix == "task"
    assert tasks["test"].description == ""
    assert "hidden" not in tasks  # internal tasks are hidden from listings
    assert tasks["short"].description is None  # shorthand string form has no desc


def test_parse_taskfile_honors_source_file() -> None:
    tasks = parse_taskfile(TASKFILE, source_file="Taskfile.yaml")
    assert tasks
    assert all(t.source_file == "Taskfile.yaml" for t in tasks)


def test_parse_taskfile_non_mapping_returns_empty() -> None:
    assert parse_taskfile("- just\n- a\n- list\n") == []
    assert parse_taskfile("version: '3'\ntasks: not-a-mapping\n") == []


def test_detect_yml(tmp_path) -> None:
    (tmp_path / "Taskfile.yml").write_text("version: '3'\n")
    assert TaskfileProvider().detect(tmp_path)


def test_detect_yaml(tmp_path) -> None:
    (tmp_path / "Taskfile.yaml").write_text("version: '3'\n")
    assert TaskfileProvider().detect(tmp_path)


def test_detect_false(tmp_path) -> None:
    assert not TaskfileProvider().detect(tmp_path)


def test_discover_reads_yaml(tmp_path) -> None:
    (tmp_path / "Taskfile.yml").write_text(TASKFILE)
    names = {t.name for t in TaskfileProvider().discover(tmp_path)}
    assert "build" in names
    assert "test" in names
    assert "hidden" not in names


def test_discover_uses_detected_yaml_taskfile_as_source(tmp_path) -> None:
    (tmp_path / "Taskfile.yaml").write_text(TASKFILE)
    tasks = TaskfileProvider().discover(tmp_path)
    assert tasks
    assert all(t.source_file == "Taskfile.yaml" for t in tasks)


def test_discover_malformed_yaml_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "Taskfile.yml").write_text("tasks: [unterminated\n")
    assert TaskfileProvider().discover(tmp_path) == []
    assert any("Taskfile" in r.message for r in caplog.records)


def test_discovery_does_not_execute_taskfile(tmp_path) -> None:
    """Security: discovery must not evaluate dynamic `sh:` vars (no runner)."""
    sentinel = tmp_path / "PWNED"
    (tmp_path / "Taskfile.yml").write_text(
        "version: '3'\n"
        "vars:\n"
        "  BOOM:\n"
        f"    sh: touch {sentinel}\n"
        "tasks:\n"
        "  build:\n"
        "    desc: build\n"
        "    cmds: [echo hi]\n"
    )
    tasks = TaskfileProvider().discover(tmp_path)
    assert not sentinel.exists()  # the `sh:` var must NOT have run
    assert [t.name for t in tasks] == ["build"]


def test_discover_missing_file_is_skipped(tmp_path):
    # discover() called with no Taskfile present hits the read-error guard.
    assert TaskfileProvider().discover(tmp_path) == []
