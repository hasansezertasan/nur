import json
from typing import Never

from nur.discovery import discover
from nur.providers import PROVIDERS


def test_providers_registry_order() -> None:
    assert [p.prefix for p in PROVIDERS] == [
        "npm",
        "make",
        "pdm",
        "poe",
        "just",
        "task",
        "mise",
        "xc",
    ]


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
