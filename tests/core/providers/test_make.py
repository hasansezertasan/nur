from nur.core.providers.make import MakeProvider, parse_descriptions, parse_targets

MAKEFILE_TEXT = """\
VERSION := 1.0
.PHONY: build test

build: main.o ## Build the binary
\tgcc -o build main.o

test: ## Run the suite
\tpytest

internal:
\techo hi

%.o: %.c
\tgcc -c $<
"""


def test_parse_targets_extracts_real_targets() -> None:
    names = parse_targets(MAKEFILE_TEXT)
    assert "build" in names
    assert "test" in names
    assert "internal" in names


def test_parse_targets_filters_specials_patterns_and_assignments() -> None:
    names = parse_targets(MAKEFILE_TEXT)
    assert not any(n.startswith(".") for n in names)  # .PHONY excluded
    assert not any("%" in n for n in names)  # pattern rules excluded
    assert "VERSION" not in names  # `:=` assignment is not a target


def test_parse_targets_dedupes_preserving_order() -> None:
    text = "build:\n\techo a\nbuild:\n\techo b\ntest:\n\techo c\n"
    assert parse_targets(text) == ["build", "test"]


def test_parse_targets_and_descriptions_handle_multi_target_rules() -> None:
    text = "build test: ## Build and test\n\techo hi\n"
    assert parse_targets(text) == ["build", "test"]
    assert parse_descriptions(text) == {
        "build": "Build and test",
        "test": "Build and test",
    }


def test_parse_descriptions() -> None:
    assert parse_descriptions(MAKEFILE_TEXT) == {
        "build": "Build the binary",
        "test": "Run the suite",
    }


def test_detect(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(MAKEFILE_TEXT)
    assert MakeProvider().detect(tmp_path)
    assert not MakeProvider().detect(tmp_path / "nope")


def test_discover_combines_targets_and_descriptions(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(MAKEFILE_TEXT)
    tasks = {t.name: t for t in MakeProvider().discover(tmp_path)}
    assert tasks["build"].argv_base == ("make", "build")
    assert tasks["build"].description == "Build the binary"
    assert tasks["build"].source_file == "Makefile"
    assert tasks["internal"].description is None


def test_discover_unreadable_makefile_returns_empty(tmp_path, caplog) -> None:
    # A directory named "Makefile" makes read_text raise OSError.
    (tmp_path / "Makefile").mkdir()
    assert MakeProvider().discover(tmp_path) == []
    assert any("Makefile" in r.message for r in caplog.records)


def test_discovery_does_not_execute_makefile(tmp_path) -> None:
    """Security: discovering tasks must never run `$(shell ...)` side effects."""
    sentinel = tmp_path / "PWNED"
    (tmp_path / "Makefile").write_text(
        f"BOOM := $(shell touch {sentinel})\nall: ## default\n\t@:\n"
    )
    tasks = MakeProvider().discover(tmp_path)
    assert not sentinel.exists()  # the $(shell ...) must NOT have run
    assert [t.name for t in tasks] == ["all"]
