from nur.core.providers.pre_commit import PreCommitProvider, parse_pre_commit_config

CONFIG = """
ci:
  autofix_prs: true
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.14.0
    hooks:
      - id: ruff-check
        name: Ruff check
        entry: ignored-for-remote
  - repo: local
    hooks:
      - id: test
        name: Run tests
        language: system
        entry: uv run pytest
  - repo: meta
    hooks:
      - id: check-hooks-apply
"""


def test_parse_pre_commit_config() -> None:
    tasks = {task.name: task for task in parse_pre_commit_config(CONFIG)}
    assert tasks["ruff-check"].argv_base == (
        "pre-commit",
        "run",
        "ruff-check",
        "--all-files",
    )
    assert tasks["ruff-check"].description == "Ruff check"
    assert tasks["ruff-check"].definition == ""
    assert tasks["test"].description == "Run tests"
    assert tasks["test"].definition == "uv run pytest"
    assert tasks["check-hooks-apply"].definition == ""
    assert all(task.prefix == "pre-commit" for task in tasks.values())


def test_parse_only_reads_repos_hooks() -> None:
    config = """
default_language_version:
  python: python3.14
ci:
  id: not-a-hook
repos:
  - repo: local
    hooks:
      - id: lint
        entry: ruff check .
"""
    assert [task.name for task in parse_pre_commit_config(config)] == ["lint"]


def test_parse_invalid_shapes_are_skipped() -> None:
    assert parse_pre_commit_config("repos: not-a-list") == []
    assert parse_pre_commit_config("repos:\n  - repo: local\n    hooks: nope") == []


def test_detect_and_discover(tmp_path) -> None:
    path = tmp_path / ".pre-commit-config.yaml"
    path.write_text(CONFIG)
    provider = PreCommitProvider()
    assert provider.detect(tmp_path)
    tasks = provider.discover(tmp_path)
    assert {task.name for task in tasks} == {"ruff-check", "test", "check-hooks-apply"}
    assert all(task.source_file == ".pre-commit-config.yaml" for task in tasks)


def test_detect_false(tmp_path) -> None:
    assert not PreCommitProvider().detect(tmp_path)


def test_discover_malformed_yaml_returns_empty(tmp_path, caplog) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text("repos: [unterminated")
    assert PreCommitProvider().discover(tmp_path) == []
    assert any(".pre-commit-config.yaml" in record.message for record in caplog.records)
