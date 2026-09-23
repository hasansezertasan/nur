import sys
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
description =
    py39: run Python 3.9 tests
    py310: run Python 3.10 tests
commands = pytest
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py39", "py310"}
    assert tasks["py39"].description == "run Python 3.9 tests"
    assert tasks["py310"].description == "run Python 3.10 tests"
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
    _write(tmp_path, "setup.cfg", "[tox:tox]\nenvlist = cfg\n")
    _write(tmp_path, "pyproject.toml", '[tool.tox]\nenv_list = ["pyproject"]\n')
    _write(tmp_path, "tox.toml", 'env_list = ["toml"]\n')
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"ini"}

    (tmp_path / "tox.ini").unlink()
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"cfg"}

    (tmp_path / "setup.cfg").unlink()
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"pyproject"}

    (tmp_path / "pyproject.toml").unlink()
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"toml"}


def test_malformed_higher_priority_toml_stops_discovery(tmp_path) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.tox\nenv_list = ['broken']\n")
    _write(tmp_path, "tox.toml", 'env_list = ["fallback"]\n')
    assert ToxProvider().discover(tmp_path) == []


def test_ini_brace_alternatives_strip_whitespace(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = py{39, 310}
[testenv:py{39, 310}]
commands = pytest
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py39", "py310"}


def test_toml_ignores_empty_command_groups(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["test"]
[env.test]
commands = [[], ["pytest"]]
""",
    )
    task = ToxProvider().discover(tmp_path)[0]
    assert task.definition == "pytest"


def test_tasks_include_passthrough_prefix(tmp_path) -> None:
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = lint\n")
    ini_task = ToxProvider().discover(tmp_path)[0]
    assert ini_task.passthrough_prefix == ("--",)
    assert ini_task.run_argv(["--watch"]) == ["tox", "-e", "lint", "--", "--watch"]
    assert ini_task.run_argv() == ["tox", "-e", "lint"]

    (tmp_path / "tox.ini").unlink()
    _write(tmp_path, "tox.toml", 'env_list = ["test"]\n')
    toml_task = ToxProvider().discover(tmp_path)[0]
    assert toml_task.passthrough_prefix == ("--",)
    assert toml_task.run_argv(["--watch"]) == ["tox", "-e", "test", "--", "--watch"]
    assert toml_task.run_argv() == ["tox", "-e", "test"]


def test_pyproject_legacy_tox_ini_is_discovered(tmp_path) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        """[tool.tox]
legacy_tox_ini = '''
[tox]
envlist = py39, py310

[testenv]
commands = pytest
'''
""",
    )
    provider = ToxProvider()
    assert provider.detect(tmp_path)
    tasks = {task.name: task for task in provider.discover(tmp_path)}
    assert set(tasks) == {"py39", "py310"}
    assert tasks["py39"].definition == "pytest"
    assert tasks["py39"].source_file == "pyproject.toml"


def test_pyproject_malformed_legacy_tox_ini_is_ignored(tmp_path, caplog) -> None:
    _write(
        tmp_path,
        "pyproject.toml",
        """[tool.tox]
legacy_tox_ini = '''
[tox
envlist = py39
'''
""",
    )
    provider = ToxProvider()
    assert not provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []
    assert any("pyproject.toml" in record.message for record in caplog.records)


def test_non_utf8_config_is_ignored(tmp_path, caplog) -> None:
    (tmp_path / "tox.ini").write_bytes(b"\xff\xfe[tox]\nenvlist = broken\n")
    provider = ToxProvider()
    assert not provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []
    assert any("tox.ini" in record.message for record in caplog.records)

    (tmp_path / "tox.ini").unlink()
    (tmp_path / "tox.toml").write_bytes(b"\xff\xfeenv_list = ['broken']\n")
    assert not provider.detect(tmp_path)
    assert provider.discover(tmp_path) == []
    assert any("tox.toml" in record.message for record in caplog.records)


def test_ini_preserves_explicit_empty_overrides(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = base_env, empty_env

[testenv]
description = base description
commands = pytest

[testenv:empty_env]
description =
commands =
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["base_env"].description == "base description"
    assert tasks["base_env"].definition == "pytest"
    assert tasks["empty_env"].description == ""
    assert tasks["empty_env"].definition == ""


def test_toml_preserves_explicit_empty_overrides(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["base_env", "empty_env"]

[env_run_base]
description = "base description"
commands = [["pytest"]]

[env.empty_env]
description = ""
commands = []
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["base_env"].description == "base description"
    assert tasks["base_env"].definition == "pytest"
    assert tasks["empty_env"].description == ""
    assert tasks["empty_env"].definition == ""


def test_default_py_env_discovered_when_envlist_omitted(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[testenv]
description = run tests
commands = pytest
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py"}
    assert tasks["py"].description == "run tests"
    assert tasks["py"].definition == "pytest"

    (tmp_path / "tox.ini").unlink()
    _write(
        tmp_path,
        "tox.toml",
        """
[env_run_base]
description = "toml base tests"
commands = [["pytest"]]
""",
    )
    toml_tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(toml_tasks) == {"py"}
    assert toml_tasks["py"].description == "toml base tests"
    assert toml_tasks["py"].definition == "pytest"


def test_explicit_empty_envlist_yields_no_tasks(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist =
[testenv]
commands = pytest
""",
    )
    assert ToxProvider().discover(tmp_path) == []

    (tmp_path / "tox.ini").unlink()
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = []
[env_run_base]
commands = [["pytest"]]
""",
    )
    assert ToxProvider().discover(tmp_path) == []


def test_commands_pre_and_post_ordering_and_inheritance(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = full,override_pre,no_pre
[testenv]
commands_pre = echo "pre"
commands = echo "main"
commands_post = echo "post"

[testenv:full]
description = full env

[testenv:override_pre]
commands_pre = echo "custom pre"
commands = echo "custom main"

[testenv:no_pre]
commands_pre =
commands = echo "no pre"
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["full"].definition == 'echo "pre" && echo "main" && echo "post"'
    assert (
        tasks["override_pre"].definition
        == 'echo "custom pre" && echo "custom main" && echo "post"'
    )
    assert tasks["no_pre"].definition == 'echo "no pre" && echo "post"'

    (tmp_path / "tox.ini").unlink()
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["full", "override_pre", "no_pre"]

[env_run_base]
commands_pre = [["echo", "pre"]]
commands = [["echo", "main"]]
commands_post = [["echo", "post"]]

[env.full]
description = "full env"

[env.override_pre]
commands_pre = [["echo", "custom pre"]]
commands = [["echo", "custom main"]]

[env.no_pre]
commands_pre = []
commands = [["echo", "no pre"]]
""",
    )
    toml_tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert toml_tasks["full"].definition == "echo pre && echo main && echo post"
    assert (
        toml_tasks["override_pre"].definition
        == "echo custom pre && echo custom main && echo post"
    )
    assert toml_tasks["no_pre"].definition == "echo no pre && echo post"


def test_toml_base_chain_resolution(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = ["foo", "no_base", "bar"]
[env_run_base]
description = "base description"
commands = [["echo", "base"]]

[env.custom_base]
description = "custom base description"
commands = [["echo", "custom"]]

[env.foo]
base = ["custom_base"]

[env.no_base]
base = []

[env.bar]
commands = [["echo", "bar"]]
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["foo"].description == "custom base description"
    assert tasks["foo"].definition == "echo custom"
    assert tasks["no_base"].description is None
    assert tasks["no_base"].definition == ""
    assert tasks["bar"].description == "base description"
    assert tasks["bar"].definition == "echo bar"


def test_ini_base_chain_resolution(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = foo, no_base, bar

[testenv]
description = base description
commands = echo base

[testenv:custom_base]
description = custom base description
commands = echo custom

[base]
sections = testenv:custom_base

[testenv:foo]
base = {[base]sections}

[testenv:no_base]
base =

[testenv:bar]
commands = echo bar
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["foo"].description == "custom base description"
    assert tasks["foo"].definition == "echo custom"
    assert tasks["no_base"].description is None
    assert tasks["no_base"].definition == ""
    assert tasks["bar"].description == "base description"
    assert tasks["bar"].definition == "echo bar"


def test_ini_factor_conditional_base_chain(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = py39, py310

[testenv:legacy]
commands = pytest tests/legacy

[testenv:modern]
commands = pytest tests/modern

[testenv:py39]
base =
    py39: testenv:legacy
    py310: testenv:modern

[testenv:py310]
base =
    py39: testenv:legacy
    py310: testenv:modern
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert tasks["py39"].definition == "pytest tests/legacy"
    assert tasks["py310"].definition == "pytest tests/modern"


def test_ini_and_toml_exclude_configured_internal_environments(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = py310
provision_tox_env = my_prov
package_env = my_pkg
wheel_build_env = my_wheel

[testenv:py310]
commands = pytest

[testenv:my_prov]
commands = echo prov

[testenv:my_pkg]
commands = echo pkg

[testenv:my_wheel]
commands = echo wheel
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {"py310"}

    # TOML configured internals
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    _write(
        subdir,
        "tox.toml",
        """
env_list = ["test"]
provision_tox_env = "my_toml_prov"
package_env = "my_toml_pkg"
wheel_build_env = "my_toml_wheel"

[env.test]
commands = [["pytest"]]

[env.my_toml_prov]
commands = [["echo", "prov"]]

[env.my_toml_pkg]
commands = [["echo", "pkg"]]

[env.my_toml_wheel]
commands = [["echo", "wheel"]]
""",
    )
    tasks_toml = {task.name: task for task in ToxProvider().discover(subdir)}
    assert set(tasks_toml) == {"test"}


def test_setup_cfg_ignores_internal_env_keys_in_unrelated_sections(tmp_path) -> None:
    _write(
        tmp_path,
        "setup.cfg",
        """
[tox:tox]
envlist = lint
[unrelated]
package_env = lint
wheel_build_env = lint
""",
    )
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {"lint"}


def test_ini_substitutions_in_envlist(tmp_path, monkeypatch) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist =
    {env:TOXENV_SET:py_default},
    {env:TOXENV_UNSET:py_fallback},
    {env:UNSET_NO_DEFAULT},
    {[testenv]custom_env},
    py310

[testenv]
custom_env = py_custom
commands = pytest

[testenv:py_override]
commands = pytest

[testenv:py_fallback]
commands = pytest

[testenv:py_custom]
commands = pytest

[testenv:py310]
commands = pytest
""",
    )
    monkeypatch.setenv("TOXENV_SET", "py_override, py_extra")
    monkeypatch.delenv("TOXENV_UNSET", raising=False)
    monkeypatch.delenv("UNSET_NO_DEFAULT", raising=False)

    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert set(tasks) == {
        "py_override",
        "py_extra",
        "py_fallback",
        "py_custom",
        "py310",
    }


def test_ini_preserves_empty_brace_alternatives(tmp_path) -> None:
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = py{310,311}-{,django42}\n")
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py310",
        "py310-django42",
        "py311",
        "py311-django42",
    }
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = py{,39}-django\n")
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py-django",
        "py39-django",
    }


def test_ini_expands_open_numeric_ranges(tmp_path) -> None:
    _write(tmp_path, "tox.ini", "[tox]\nenvlist = py3{10-}, legacy3{-13}\n")
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py310",
        "py311",
        "py312",
        "py313",
        "py314",
        "legacy310",
        "legacy311",
        "legacy312",
        "legacy313",
    }


def test_toml_resolves_env_list_replacements(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TOX_ENV_LIST", "py312, lint")
    _write(
        tmp_path,
        "tox.toml",
        """
env_list = [
  { replace = "env", name = "TOX_ENV_LIST", default = ["py311"], extend = true },
  { replace = "env", name = "MISSING", default = ["type", "docs"], extend = true },
]
""",
    )
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py312",
        "lint",
        "type",
        "docs",
    }


def test_ini_multiline_base_chain(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = child
[testenv:shared]
description = inherited
[testenv:child]
base =
    testenv:missing
    testenv:shared
""",
    )
    task = ToxProvider().discover(tmp_path)[0]
    assert task.description == "inherited"


def test_ini_and_toml_include_label_only_environments(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist =
labels =
    test = py{310,311}
""",
    )
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py310",
        "py311",
    }

    (tmp_path / "tox.ini").unlink()
    _write(
        tmp_path,
        "tox.toml",
        'env_list = []\nlabels = { test = ["py310", "py311"], lint = "ruff" }\n',
    )
    assert {task.name for task in ToxProvider().discover(tmp_path)} == {
        "py310",
        "py311",
        "ruff",
    }


def test_ini_commands_factor_conditions(tmp_path) -> None:
    _write(
        tmp_path,
        "tox.ini",
        """
[tox]
envlist = py39, py310, py310-django, py39-django

[testenv]
commands_pre =
    py39: echo pre39
    py310: echo pre310
commands =
    py39: pytest tests/legacy
    py310: pytest tests/current
    {py39,py310}-django: pytest tests/django
    !py39: pytest tests/non_py39
    echo common
commands_post =
    py39: echo post39
    py310: echo post310
""",
    )
    tasks = {task.name: task for task in ToxProvider().discover(tmp_path)}
    assert (
        tasks["py39"].definition
        == "echo pre39 && pytest tests/legacy && echo common && echo post39"
    )
    expected_310 = (
        "echo pre310 && pytest tests/current && pytest tests/non_py39 "
        "&& echo common && echo post310"
    )
    assert tasks["py310"].definition == expected_310
    expected_39_django = (
        "echo pre39 && pytest tests/legacy && pytest tests/django "
        "&& echo common && echo post39"
    )
    assert tasks["py39-django"].definition == expected_39_django
    expected_310_django = (
        "echo pre310 && pytest tests/current && pytest tests/django "
        "&& pytest tests/non_py39 && echo common && echo post310"
    )
    assert tasks["py310-django"].definition == expected_310_django


def test_unrelated_files_and_malformed_configs_are_ignored(tmp_path, caplog) -> None:
    _write(tmp_path, "pyproject.toml", "[tool.pdm.scripts]\ntest = 'pytest'\n")
    assert not ToxProvider().detect(tmp_path)
    _write(tmp_path, "tox.ini", "[tox\nenvlist = test")
    assert ToxProvider().discover(tmp_path) == []
    assert any("tox.ini" in record.message for record in caplog.records)


def test_tox_coverage_edge_cases(tmp_path):
    import configparser
    from pathlib import Path

    from nur.core.providers.tox import (
        _MISSING,
        _command_argument,
        _command_definition,
        _command_strings,
        _Config,
        _configured_internal_envs_ini,
        _configured_internal_envs_toml,
        _expand_env_name,
        _expand_simple_factors,
        _filter_ini_commands,
        _find_config,
        _ini_tasks,
        _resolve_ini_setting,
        _resolve_toml_setting,
        _split_envlist,
        _toml_tasks,
    )

    # 51-53: _split_envlist trailing empty
    assert _split_envlist("a, b, ") == ["a", "b"]

    # 63: _expand_env_name missing closing brace
    assert _expand_env_name("a{b") == ["a{b"]
    assert _expand_env_name("{env:UNSET_EMPTY}") == []
    assert _expand_env_name("{[nonexistent]missing}") == []
    assert _expand_env_name("py{39 310}") == ["py39", "py310"]
    assert _expand_env_name("{,py}-django") == ["django", "py-django"]
    assert _expand_env_name("py{8-10}") == ["py8", "py9", "py10"]
    assert _expand_env_name("py{08-10}") == ["py08", "py09", "py10"]

    # _expand_simple_factors edge cases
    assert _expand_simple_factors("a{b") == ["a{b"]
    assert _expand_simple_factors("{a,b}") == ["a", "b"]
    assert _filter_ini_commands(123, "py39") == ""
    assert _filter_ini_commands("   \n", "py39") == ""
    assert _filter_ini_commands("{py39,py310: pytest", "py39") == (
        "{py39,py310: pytest"
    )
    assert _filter_ini_commands("py39}: pytest", "py39") == "py39}: pytest"
    assert _filter_ini_commands(r"C:\tools\python.exe -m pytest", "py39") == (
        r"C:\tools\python.exe -m pytest"
    )
    assert _filter_ini_commands("pytest \\\n tests/unit", "py39") == "pytest tests/unit"
    assert _filter_ini_commands("linux: pytest", "py39") == (
        "pytest" if sys.platform == "linux" else ""
    )

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

    # _configured_internal_envs_ini & _toml edge cases
    cp = configparser.ConfigParser()
    cp.read_string(
        "[tox]\n"
        "isolated_build_env = iso\n"
        "[testenv:sub]\n"
        "package_env = pkg\n"
        "isolated_build_env = iso2\n"
    )
    assert {"iso", "pkg", "iso2"}.issubset(_configured_internal_envs_ini(cp, "tox"))
    assert {"t_iso", "t_base_pkg"}.issubset(
        _configured_internal_envs_toml({
            "isolated_build_env": "t_iso",
            "env_run_base": {"package_env": "t_base_pkg"},
            "env": {"extra": {"package_env": "extra_pkg"}},
        })
    )

    # _resolve_ini_setting & _resolve_toml_setting edge cases
    assert _resolve_ini_setting(None, "desc", "py", cp, set()) is None
    cycle = configparser.ConfigParser()
    cycle.read_string("[testenv:a]\nbase = testenv:a\n")
    assert _resolve_ini_setting(cycle["testenv:a"], "desc", "a", cycle, set()) is None
    assert _resolve_toml_setting("unknown", {}, "desc", {}, set()) is _MISSING
    assert (
        _resolve_toml_setting(
            "a", {"base": "a"}, "desc", {"env": {"a": {"base": "a"}}}, set()
        )
        is _MISSING
    )
    assert _resolve_toml_setting("env1", {"base": 123}, "desc", {}, set()) is _MISSING
    assert (
        _resolve_toml_setting(
            "env1",
            {"base": "b1"},
            "description",
            {"env": {"b1": {"description": "b1_desc"}}},
            set(),
        )
        == "b1_desc"
    )

    # 254: _toml_tasks wrong type and envlist fallback
    assert _toml_tasks(_Config(Path("x.toml"), "toml", 123)) == []
    assert [
        t.name
        for t in _toml_tasks(_Config(Path("x.toml"), "toml", {"envlist": ["toml_env"]}))
    ] == ["toml_env"]
    assert [
        t.name
        for t in _toml_tasks(
            _Config(Path("x.toml"), "toml", {"env_list": "str_env1, str_env2"})
        )
    ] == ["str_env1", "str_env2"]

    # 180: _find_config missing tox:tox in setup.cfg
    (tmp_path / "setup.cfg").write_text("[other]\nfoo=bar\n")
    assert _find_config(tmp_path) is None

    (tmp_path / "pyproject.toml").write_text("bad toml [\n")
    assert _find_config(tmp_path) is None
