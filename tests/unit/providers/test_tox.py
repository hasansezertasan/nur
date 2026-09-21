from typing import TYPE_CHECKING

from nur.core.providers.tox import ToxProvider

if TYPE_CHECKING:
    from pathlib import Path


def _write(tmp_path: Path, name: str, text: str) -> Path:
    (tmp_path / name).write_text(text)
    return tmp_path


def test_ini_expands_envlist_unions_sections_and_inherits_base(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = {py39,py310}-django{42,50}, py3{10-11}

[testenv]
description = base description
commands =
    pytest
    coverage report

[testenv:lint]
description = check formatting
commands = ruff check .
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {
        "py39-django42",
        "py39-django50",
        "py310-django42",
        "py310-django50",
        "py310",
        "py311",
        "lint",
    }
    assert tasks["py310"].description == "base description"
    assert tasks["py310"].definition == "pytest && coverage report"
    assert tasks["lint"].argv_base == ("tox", "-e", "lint")
    assert tasks["lint"].source_file == "tox.ini"


def test_ini_expands_generative_sections_and_uses_their_metadata(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = py39,py310

[testenv:py{39,310}]
description = run supported Python versions
commands = pytest
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py39", "py310"}
    assert tasks["py39"].description == "run supported Python versions"
    assert tasks["py310"].definition == "pytest"


def test_toml_uses_literal_names_and_inherits_env_run_base(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["py{310,311}", "lint", ".pkg", ".tox"]
[env_run_base]
description = "base description"
commands = [["python", "-m", "pytest"], ["coverage", "report"]]
[env.lint]
description = "run linter"
commands = [["ruff", "check", "."]]
[env.extra]
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py{310,311}", "lint", "extra"}
    assert tasks["py{310,311}"].definition == "python -m pytest && coverage report"
    assert tasks["lint"].description == "run linter"
    assert tasks["extra"].description == "base description"


def test_toml_renders_posargs_replacement_objects(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["test", "cli"]
[env.test]
commands = [["pytest", { replace = "posargs", default = ["tests", "-v"], """
        """extend = true }]]
[env.cli]
commands = [["nur", { replace = "posargs", default = [], extend = true }]]
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["test"].definition == "pytest {posargs:tests -v}"
    assert tasks["cli"].definition == "nur {posargs}"


def test_toml_renders_parameterized_replacement_objects(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["publish"]
[env.publish]
commands = [[
  "twine",
  "upload",
  { replace = "glob", pattern = "dist/*.whl", default = ["fallback.whl"], """
        """extend = true },
  { replace = "env", name = "REPOSITORY", default = "testpypi" },
  { replace = "ref", env = "publish", key = "package" },
]]
""",
    )
    task = ToxProvider().discover(tmp_path)[0]
    assert task.definition == (
        "twine upload {glob:dist/*.whl:fallback.whl} "
        "{env:REPOSITORY:testpypi} {[publish]package}"
    )


def test_toml_renders_top_level_posargs_replacement_objects(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["test"]
[env.test]
commands = [
  { replace = "posargs", default = [["python", "patch.py"]] },
  ["pytest"],
]
""",
    )
    task = ToxProvider().discover(tmp_path)[0]
    assert task.definition == "python patch.py && pytest"


def test_pyproject_and_setup_cfg_are_detected(tmp_path) -> None:
    _write(tmp_path, "pyproject.toml", '[tool.tox]\nenv_list = ["test"]\n')
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"test"}
    (tmp_path / "pyproject.toml").unlink()
    _write(
        tmp_path,
        "setup.cfg",
        "[tox:tox]\nenvlist = lint\n[testenv:lint]\ncommands = ruff check .\n",
    )
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"lint"}


def test_first_matching_config_wins(tmp_path) -> None:
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = ini\n")
    _write(tmp_path, "tox.toml", 'env_list = ["toml"]\n')
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"ini"}


def test_unrelated_files_and_malformed_configs_are_ignored(tmp_path, caplog) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.pdm.scripts]\ntest = 'pytest'\n")
    assert not ToxProvider().detect(tmp_path)
    _write(tmp_path, "tox.ini", "[tox\nenvlist = test")
    assert ToxProvider().discover(tmp_path) == []
    assert any("tox.ini" in record.message for record in caplog.records)


def test_tox_coverage_edge_cases(tmp_path):
    from pathlib import Path

    from nur.core.providers.tox import (
        _command_argument,
        _command_definition,
        _command_strings,
        _Config,
        _expand_env_name,
        _find_config,
        _ini_tasks,
        _split_envlist,
        _toml_tasks,
    )

    # 51-53: _split_envlist trailing empty
    assert _split_envlist("a, b, ") == ["a", "b"]

    # 63: _expand_env_name missing closing brace
    assert _expand_env_name("a{b") == ["a{b"]

    # 109-111: ref with 'of'
    assert (
        _command_argument({"replace": "ref", "of": ["foo", "bar"]}) == "{ref:foo.bar}"
    )
    assert _command_argument({"replace": "env"}) == "{env:None}"

    # 117, 121, 124
    assert _command_strings("just_string") == ["just_string"]
    assert _command_strings(123) == []
    assert _command_strings({"replace": "other"}) == ["{other}"]
    assert _command_strings({"replace": "posargs", "default": "not_list"}) == [
        "{posargs:not_list}"
    ]

    # 148-150
    assert _command_definition([123, "cmd"]) == "cmd"
    assert _command_definition(123) == ""
    assert _command_definition("line1\nline2\n \nline3") == "line1 && line2 && line3"

    # 198: _ini_tasks wrong type
    assert _ini_tasks(_Config(Path("x.ini"), "ini", {})) == []

    # 254: _toml_tasks wrong type
    assert _toml_tasks(_Config(Path("x.toml"), "toml", 123)) == []

    # 180: _find_config missing tox:tox in setup.cfg
    (tmp_path / "setup.cfg").write_text("[other]\nfoo=bar\n")
    assert _find_config(tmp_path) is None

    (tmp_path / "pyproject.toml").write_text("bad toml [\n")
    assert _find_config(tmp_path) is None
