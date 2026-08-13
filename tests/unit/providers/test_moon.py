from nur.core.providers.moon import MoonProvider, parse_moon

MOON_YML = """
tasks:
  build:
    description: Build it
    command: vite build
  lint:
    command: eslint
    args:
      - --fix
      - .
  test:
    description: ""
    script: 'jest --coverage && echo done'
  bundle:
    command:
      - rollup
      - -c
    args: --silent
  private:
    command: echo secret
    options:
      internal: true
  compose:
    deps:
      - build
"""


def test_parse_moon() -> None:
    tasks = {t.name: t for t in parse_moon(MOON_YML)}
    assert tasks["build"].argv_base == ("moon", "run", "build")
    assert tasks["build"].prefix == "moon"
    assert tasks["build"].description == "Build it"
    assert tasks["build"].definition == "vite build"
    assert tasks["build"].source_file == "moon.yml"


def test_parse_moon_forwards_args_after_separator() -> None:
    build = {t.name: t for t in parse_moon(MOON_YML)}["build"]
    # moon requires `moon run <task> -- <args>` to reach the underlying task.
    assert build.run_argv(["--watch"]) == ["moon", "run", "build", "--", "--watch"]
    # with no extra args the separator is omitted.
    assert build.run_argv() == ["moon", "run", "build"]


def test_parse_moon_command_and_args_forms() -> None:
    tasks = {t.name: t for t in parse_moon(MOON_YML)}
    # string command + list args
    assert tasks["lint"].definition == "eslint --fix ."
    # list command + string args
    assert tasks["bundle"].definition == "rollup -c --silent"
    # script form is used when no command is present
    assert tasks["test"].definition == "jest --coverage && echo done"
    assert tasks["test"].description == ""


def test_parse_moon_hides_internal_tasks() -> None:
    tasks = {t.name: t for t in parse_moon(MOON_YML)}
    assert "private" not in tasks  # options.internal tasks can't be run from the CLI


def test_parse_moon_task_without_command_or_script() -> None:
    tasks = {t.name: t for t in parse_moon(MOON_YML)}
    # a deps-only task has no local command to preview
    assert tasks["compose"].definition == ""
    assert tasks["compose"].description is None


def test_parse_moon_honors_source_file() -> None:
    tasks = parse_moon(MOON_YML, source_file="packages/app/moon.yml")
    assert tasks
    assert all(t.source_file == "packages/app/moon.yml" for t in tasks)


def test_parse_moon_non_mapping_returns_empty() -> None:
    assert parse_moon("- just\n- a\n- list\n") == []
    assert parse_moon("tasks: not-a-mapping\n") == []
    assert parse_moon("tasks:\n  build: just-a-string\n") == []  # tasks must be maps


def test_detect_moon_yml(tmp_path) -> None:
    (tmp_path / "moon.yml").write_text("tasks: {}\n")
    assert MoonProvider().detect(tmp_path)


def test_detect_false(tmp_path) -> None:
    assert not MoonProvider().detect(tmp_path)
    # moon uses the .yml extension only; a .yaml file is not a moon config.
    (tmp_path / "moon.yaml").write_text("tasks: {}\n")
    assert not MoonProvider().detect(tmp_path)


def test_discover_reads_yaml(tmp_path) -> None:
    (tmp_path / "moon.yml").write_text(MOON_YML)
    names = {t.name for t in MoonProvider().discover(tmp_path)}
    assert "build" in names
    assert "lint" in names
    assert "private" not in names


def test_discover_malformed_yaml_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / "moon.yml").write_text("tasks: [unterminated\n")
    assert MoonProvider().discover(tmp_path) == []
    assert any("moon.yml" in r.message for r in caplog.records)


def test_discover_missing_file_is_skipped(tmp_path) -> None:
    # discover() called with no moon.yml present hits the read-error guard.
    assert MoonProvider().discover(tmp_path) == []
