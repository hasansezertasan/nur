import json
from typing import TYPE_CHECKING

from nur.core.providers.composer import ComposerProvider

if TYPE_CHECKING:
    from pathlib import Path

MANIFEST = {
    "name": "vendor/pkg",
    "scripts": {
        "test": "phpunit",
        "lint": ["php-cs-fixer fix", "phpstan analyse"],
        "cs": "MyVendor\\CodeStyle::check",
        "ci": ["@lint", "@test"],
        # Reserved lifecycle hook — must be filtered out.
        "post-install-cmd": "MyVendor\\Installer::postInstall",
    },
    "scripts-descriptions": {"test": "Run the test suite"},
}


def _write(tmp_path: Path, data: object, name: str = "composer.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    assert ComposerProvider().detect(tmp_path)


def test_detect_false_without_file(tmp_path) -> None:
    assert not ComposerProvider().detect(tmp_path)


def test_detect_false_without_scripts(tmp_path) -> None:
    _write(tmp_path, {"name": "vendor/pkg"})
    assert not ComposerProvider().detect(tmp_path)


def test_detect_false_on_empty_scripts(tmp_path) -> None:
    _write(tmp_path, {"scripts": {}})
    assert not ComposerProvider().detect(tmp_path)


def test_detect_false_when_only_reserved_hooks(tmp_path) -> None:
    _write(tmp_path, {"scripts": {"post-install-cmd": "x", "pre-update-cmd": "y"}})
    assert not ComposerProvider().detect(tmp_path)


def test_discover_string_script(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    assert tasks["test"].argv_base == ("composer", "run-script", "test")
    assert tasks["test"].definition == "phpunit"
    assert tasks["test"].description == "Run the test suite"
    assert tasks["test"].source_file == "composer.json"
    assert tasks["test"].prefix == "composer"


def test_passthrough_inserts_composer_separator(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    assert tasks["test"].run_argv(["--filter", "unit"]) == [
        "composer",
        "run-script",
        "test",
        "--",
        "--filter",
        "unit",
    ]
    # No separator is emitted when there is nothing to forward.
    assert tasks["test"].run_argv() == ["composer", "run-script", "test"]


def test_discover_array_script_joined_with_and(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    assert tasks["lint"].definition == "php-cs-fixer fix && phpstan analyse"


def test_discover_php_callback_is_opaque(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    # The PHP callback is surfaced by name; its definition is left verbatim.
    assert tasks["cs"].definition == "MyVendor\\CodeStyle::check"
    assert tasks["cs"].description is None


def test_discover_at_references_left_verbatim(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    assert tasks["ci"].definition == "@lint && @test"


def test_discover_filters_reserved_hooks(tmp_path) -> None:
    _write(tmp_path, MANIFEST)
    names = {t.name for t in ComposerProvider().discover(tmp_path)}
    assert names == {"test", "lint", "cs", "ci"}
    assert "post-install-cmd" not in names


def test_non_string_non_array_scripts_are_skipped(tmp_path) -> None:
    _write(tmp_path, {"scripts": {"good": "echo", "num": 1, "obj": {}, "flag": True}})
    names = {t.name for t in ComposerProvider().discover(tmp_path)}
    assert names == {"good"}


def test_array_non_string_entries_are_dropped(tmp_path) -> None:
    _write(tmp_path, {"scripts": {"mixed": ["echo a", 5, "echo b"]}})
    tasks = {t.name: t for t in ComposerProvider().discover(tmp_path)}
    assert tasks["mixed"].definition == "echo a && echo b"


def test_scripts_not_object_returns_empty(tmp_path) -> None:
    _write(tmp_path, {"scripts": ["nope"]})
    assert ComposerProvider().discover(tmp_path) == []


def test_discover_empty_without_file(tmp_path) -> None:
    assert ComposerProvider().discover(tmp_path) == []


def test_absent_file_emits_no_warning(tmp_path, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="nur"):
        assert not ComposerProvider().detect(tmp_path)
        assert ComposerProvider().discover(tmp_path) == []
    assert caplog.records == []


def test_malformed_json_returns_empty(tmp_path) -> None:
    _write(tmp_path, '{"scripts": {')
    assert ComposerProvider().discover(tmp_path) == []


def test_malformed_json_emits_warning(tmp_path, caplog) -> None:
    import logging

    _write(tmp_path, '{"scripts": {')
    with caplog.at_level(logging.WARNING, logger="nur"):
        ComposerProvider().discover(tmp_path)
    assert any("composer.json" in r.message for r in caplog.records)
