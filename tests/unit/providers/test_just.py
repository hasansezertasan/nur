from __future__ import annotations

from nur.core.providers.just import JustProvider, parse_justfile

JUSTFILE = """\
# Build the project
build:
    cargo build

# Run the tests
[group('ci')]
test *args:
    cargo test {{args}}

# not a doc

deploy env='prod': build test
    ./deploy.sh {{env}}

alias b := build
export VERSION := "1.0"
set shell := ["bash", "-c"]

@quiet:
    echo hi

[private]
[doc('Internal helper')]
_helper:
    echo secret
"""


def test_parse_justfile_extracts_recipe_names() -> None:
    names = {t.name for t in parse_justfile(JUSTFILE)}
    assert names == {"build", "test", "deploy", "quiet", "_helper"}


def test_parse_justfile_uses_leading_comment_as_description() -> None:
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["build"].description == "Build the project"
    assert tasks["test"].description == "Run the tests"


def test_parse_justfile_ignores_comment_separated_by_blank_line() -> None:
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["deploy"].description is None


def test_parse_justfile_reads_doc_attribute() -> None:
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["_helper"].description == "Internal helper"


def test_parse_justfile_skips_attributes_over_comment() -> None:
    # The `[group(...)]` attribute sits between the comment and the recipe;
    # the comment must still resolve as the doc.
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["test"].description == "Run the tests"


def test_parse_justfile_ignores_assignments_and_settings() -> None:
    names = {t.name for t in parse_justfile(JUSTFILE)}
    assert "VERSION" not in names
    assert "shell" not in names
    assert "set" not in names
    assert "alias" not in names
    assert "export" not in names


def test_parse_justfile_handles_at_prefix() -> None:
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["quiet"].argv_base == ("just", "quiet")


def test_parse_justfile_sets_argv_and_prefix() -> None:
    tasks = {t.name: t for t in parse_justfile(JUSTFILE)}
    assert tasks["build"].argv_base == ("just", "build")
    assert tasks["build"].prefix == "just"


def test_parse_justfile_honors_source_file() -> None:
    tasks = parse_justfile(JUSTFILE, source_file="Justfile")
    assert tasks
    assert all(t.source_file == "Justfile" for t in tasks)


def test_parse_justfile_empty_text_returns_empty() -> None:
    assert parse_justfile("") == []


def test_parse_justfile_ignores_recipe_like_lines_in_multiline_string() -> None:
    text = '''\
message := """
phantom:
    not a recipe
"""

real:
    echo hi
'''
    names = {t.name for t in parse_justfile(text)}
    assert names == {"real"}


def test_parse_justfile_ignores_recipe_like_lines_in_backtick_block() -> None:
    text = """\
result := ```
fake:
    echo nope
```

build:
    echo hi
"""
    names = {t.name for t in parse_justfile(text)}
    assert names == {"build"}


def test_parse_justfile_recovers_after_multiline_literal() -> None:
    text = '''\
first:
    echo 1

blob := """
inside:
"""

second:
    echo 2
'''
    names = {t.name for t in parse_justfile(text)}
    assert names == {"first", "second"}


def test_parse_justfile_doc_substring_in_other_attribute_is_ignored() -> None:
    # `doc('api')` appears inside the confirm prompt, not as a doc attribute.
    text = "[confirm(\"Regenerate doc('api')?\")]\nbuild:\n    echo hi\n"
    tasks = {t.name: t for t in parse_justfile(text)}
    assert tasks["build"].description is None


def test_parse_justfile_ignores_shebang_as_description() -> None:
    text = "#!/usr/bin/env just --justfile\nbuild:\n    echo hi\n"
    tasks = {t.name: t for t in parse_justfile(text)}
    assert tasks["build"].description is None


def test_detect(tmp_path) -> None:
    (tmp_path / "justfile").write_text("build:\n  echo hi\n")
    assert JustProvider().detect(tmp_path)
    assert not JustProvider().detect(tmp_path / "nope")


def test_discover_parses_file(tmp_path) -> None:
    (tmp_path / "justfile").write_text("# Build\nbuild:\n    echo hi\n")
    tasks = {t.name: t for t in JustProvider().discover(tmp_path)}
    assert tasks["build"].description == "Build"
    assert tasks["build"].source_file == "justfile"


def test_discover_missing_file_returns_empty(tmp_path, caplog) -> None:
    assert JustProvider().discover(tmp_path) == []


def test_discover_undecodable_file_returns_empty(tmp_path, caplog) -> None:
    # Bytes invalid as UTF-8 must be handled like other providers: warn + [].
    (tmp_path / "justfile").write_bytes(b"build:\n\txxx \xff\xfe\n")
    assert JustProvider().discover(tmp_path) == []
    assert any("skipping justfile" in r.message for r in caplog.records)


def test_discover_unreadable_file_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "justfile").write_text("build:\n    echo hi\n")

    def boom(*_a, **_k) -> str:
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.read_text", boom)
    assert JustProvider().discover(tmp_path) == []
    assert any("skipping justfile" in r.message for r in caplog.records)
