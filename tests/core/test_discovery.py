import json
import tomllib
from pathlib import Path
from typing import Never

from nur.core.discovery import discover
from nur.core.providers import PROVIDERS

_ROOT = Path(__file__).resolve().parents[2]
# Every provider's PyPI keyword equals its prefix, except the Taskfile provider,
# whose keyword is spelled out.
_KEYWORD_ALIASES = {"task": "taskfile"}
# A provider's module basename equals its prefix, except where the prefix
# contains characters illegal in a Python module name (e.g. the hyphen in
# ``cargo-make`` -> ``cargo_make``).
_MODULE_ALIASES = {"cargo-make": "cargo_make", "pre-commit": "pre_commit"}


def test_providers_registry_order() -> None:
    assert [p.prefix for p in PROVIDERS] == [
        "npm",
        "deno",
        "composer",
        "make",
        "pdm",
        "poe",
        "tox",
        "just",
        "task",
        "pre-commit",
        "mise",
        "cargo-make",
        "moon",
        "xc",
        "vscode",
        "nox",
        "mask",
        "invoke",
        "rake",
    ]


def test_every_provider_is_keyworded_and_documented() -> None:
    # Guards the drift that left `mise` (and nearly `xc`) out of the PyPI
    # keywords and the API reference: adding a provider must update both.
    prefixes = {p.prefix for p in PROVIDERS}

    metadata = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    keywords = set(metadata["project"]["keywords"])
    expected = {_KEYWORD_ALIASES.get(prefix, prefix) for prefix in prefixes}
    assert expected <= keywords, f"missing PyPI keywords: {expected - keywords}"

    modules = (_ROOT / "docs" / "modules.rst").read_text(encoding="utf-8")
    for prefix in prefixes:
        module = _MODULE_ALIASES.get(prefix, prefix)
        directive = f".. automodule:: nur.core.providers.{module}"
        assert directive in modules, f"missing from docs/modules.rst: {directive}"


def test_discover_includes_xc_tasks(tmp_path) -> None:
    (tmp_path / "README.md").write_text(
        "# Demo\n\n## Tasks\n\n### build\n\n```sh\nuv build\n```\n"
    )
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite"}}))
    reg = discover(tmp_path)
    names = {t.qualified_name for t in reg.all()}
    assert {"xc:build", "npm:build"} <= names
    # A name shared with another provider stays reachable when qualified.
    assert reg.resolve("xc:build").argv_base == ("xc", "build")


def test_discover_includes_mask_subcommands(tmp_path) -> None:
    (tmp_path / "maskfile.md").write_text(
        "# Tasks\n\n## test\n\n```sh\npytest\n```\n\n"
        "### test lint\n\n```sh\nruff check\n```\n"
    )
    reg = discover(tmp_path)
    assert reg.resolve("test lint").argv_base == ("mask", "test", "lint")
    assert reg.resolve("mask:test").argv_base == ("mask", "test")


def test_discover_aggregates(tmp_path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "pyproject.toml").write_text("[tool.pdm.scripts]\nlint = 'ruff'\n")
    reg = discover(tmp_path)
    names = {t.qualified_name for t in reg.all()}
    assert "npm:test" in names
    assert "pdm:lint" in names


def test_discover_empty_dir(tmp_path) -> None:
    assert discover(tmp_path).is_empty()


def test_discover_backstop_on_provider_raise(tmp_path, caplog) -> None:
    class Boom:
        prefix = "boom"

        def detect(self, cwd) -> bool:
            return True

        def discover(self, cwd) -> Never:
            msg = "kaboom"
            raise RuntimeError(msg)

    reg = discover(tmp_path, providers=[Boom()])
    assert reg.is_empty()
    assert any("boom" in r.message for r in caplog.records)


def test_discover_includes_rake_tasks(tmp_path) -> None:
    (tmp_path / "Rakefile").write_text(
        "task :build\nnamespace :db do\n task :migrate\nend\n", encoding="utf-8"
    )
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "vite"}}))
    registry = discover(tmp_path)
    assert {"rake:build", "rake:db:migrate", "npm:build"} <= {
        task.qualified_name for task in registry.all()
    }
    assert registry.resolve("rake:build").argv_base == ("rake", "build")
    assert registry.resolve("rake:db:migrate").argv_base == ("rake", "db:migrate")
