import pytest

from nur.core.providers.make import MakeProvider

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


@pytest.fixture
def discover(tmp_path):
    def discover_makefile(text: str):
        (tmp_path / "Makefile").write_bytes(text.encode("utf-8"))
        return MakeProvider().discover(tmp_path)

    return discover_makefile


def test_discover_targets_extracts_real_targets(discover) -> None:
    names = [task.name for task in discover(MAKEFILE_TEXT)]
    assert "build" in names
    assert "test" in names
    assert "internal" in names


def test_discover_targets_filters_specials_patterns_and_assignments(discover) -> None:
    names = [task.name for task in discover(MAKEFILE_TEXT)]
    assert not any(n.startswith(".") for n in names)  # .PHONY excluded
    assert not any("%" in n for n in names)  # pattern rules excluded
    assert "VERSION" not in names  # `:=` assignment is not a target


def test_discover_targets_dedupes_preserving_order(discover) -> None:
    text = "build:\n\techo a\nbuild:\n\techo b\ntest:\n\techo c\n"
    assert [task.name for task in discover(text)] == ["build", "test"]


def test_discover_targets_and_descriptions_handle_multi_target_rules(discover) -> None:
    text = "build test: ## Build and test\n\techo hi\n"
    assert [task.name for task in discover(text)] == ["build", "test"]
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"build": "Build and test", "test": "Build and test"}


def test_discover_descriptions(discover) -> None:
    assert {
        task.name: task.description
        for task in discover(MAKEFILE_TEXT)
        if task.description
    } == {"build": "Build the binary", "test": "Run the suite"}


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
    # A directory named "Makefile" makes read_bytes raise OSError.
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


def test_discover_targets_ignores_define_blocks(discover) -> None:
    text = "define TEMPLATE\nfake: dep\nendef\n\nreal:\n\techo hi\n"
    assert [task.name for task in discover(text)] == ["real"]


def test_discover_targets_supports_continued_headers(discover) -> None:
    text = "build \\\n  test: dep \\\n  other ## Build and test\n\techo hi\n"
    assert [task.name for task in discover(text)] == ["build", "test"]
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"build": "Build and test", "test": "Build and test"}


def test_discover_targets_ignores_recipe_bodies_and_assignments(discover) -> None:
    text = (
        "FLAGS = a:b\n"
        "OUT != echo x:y\n"
        "run:\n"
        "\techo phantom: nope\n"
        "\tcurl http://host:80/\n"
    )
    assert [task.name for task in discover(text)] == ["run"]


def test_discover_targets_skips_computed_and_pattern_targets(discover) -> None:
    text = "$(NAME): dep\nx$(Y): dep\n%.o: %.c\n.PHONY: all\nall:\n"
    assert [task.name for task in discover(text)] == ["all"]


def test_discover_targets_supports_double_colon_and_static_pattern_rules(
    discover,
) -> None:
    text = "all:: ; @true\na.o b.o: %.o: %.c\n\tcc -c $<\n"
    assert [task.name for task in discover(text)] == ["all", "a.o", "b.o"]


def test_discover_targets_finds_rules_in_conditionals(discover) -> None:
    text = "ifdef FOO\ncond:\n\techo hi\nendif\nlast:\n"
    assert [task.name for task in discover(text)] == ["cond", "last"]


def test_discover_targets_excludes_makefile_target(discover) -> None:
    assert [task.name for task in discover("Makefile:\nreal:\n")] == ["real"]


def test_discover_targets_tolerates_malformed_input(discover, caplog) -> None:
    text = 'good:\n\techo ok\n\nbroken = $(shell python -c "if x(\nlater:\n'
    with caplog.at_level("WARNING", logger="nur"):
        names = [task.name for task in discover(text)]
    assert "good" in names
    assert "broken" not in names
    assert "later" in names
    assert "parser rejects" in caplog.text


def test_discover_targets_handles_empty_and_non_utf8_content(discover) -> None:
    assert [task.name for task in discover("")] == []
    assert [task.name for task in discover("# only a comment\n")] == []
    assert [task.name for task in discover("café: x\n")] == []  # no truncated name


def test_discover_does_not_execute_shell_expressions(tmp_path) -> None:
    marker = tmp_path / "marker"
    (tmp_path / "Makefile").write_text(
        f"X := $(shell touch {marker})\nY != touch {marker}\nreal:\n\ttrue\n"
    )
    tasks = MakeProvider().discover(tmp_path)
    assert [t.name for t in tasks] == ["real"]
    assert not marker.exists()


def test_discover_reads_bytes_with_non_utf8_content(tmp_path) -> None:
    (tmp_path / "Makefile").write_bytes(b"# \xff\xfe\nreal: ## Run it\n\ttrue\n")
    tasks = MakeProvider().discover(tmp_path)
    assert [(t.name, t.description) for t in tasks] == [("real", "Run it")]


def test_discover_targets_rejects_non_ascii_name_fragments(discover) -> None:
    assert [task.name for task in discover("ok:\n\techo\nüber: x\nlast:\n")] == [
        "ok",
        "last",
    ]


def test_discover_targets_accepts_files_without_trailing_newline(
    discover, caplog
) -> None:
    with caplog.at_level("WARNING", logger="nur"):
        assert [task.name for task in discover("all: b\nb:")] == ["all", "b"]
    assert "parser rejects" not in caplog.text


def test_discover_descriptions_ignore_bare_carriage_returns(discover) -> None:
    text = "x = 1\ry = 2\nfoo: ## d1\nbar:\n"
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"foo": "d1"}


def test_discover_targets_handles_deeply_nested_conditionals(discover) -> None:
    depth = 3000
    text = "ifdef A\n" * depth + "deep:\n" + "endif\n" * depth
    assert [task.name for task in discover(text)] == ["deep"]


def test_discover_targets_handles_long_files(discover) -> None:
    text = "\n" * 300 + "".join(f"t{i}:\n\ttrue\n" for i in range(300))
    assert [task.name for task in discover(text)] == [f"t{i}" for i in range(300)]


def test_discover_targets_survives_parenthesised_comments_and_values(discover) -> None:
    text = "PORT = 8080  # default port (see docs)\nbuild:\n\ttrue\ntest:\n\ttrue\n"
    assert [task.name for task in discover(text)] == ["build", "test"]
    assert [task.name for task in discover("X = foo(bar)\nbuild:\n")] == ["build"]


def test_discover_targets_survives_headers_fused_with_preceding_lines(discover) -> None:
    assert [task.name for task in discover("$(EXTRA)\nbuild:\n\ttrue\n")] == ["build"]


def test_discover_targets_lists_target_specific_variable_targets(discover) -> None:
    text = "a b: FLAGS=-x\nclean:\n\trm -f a b\n"
    assert [task.name for task in discover(text)] == ["a", "b", "clean"]
    assert [task.name for task in discover("VERSION := 1\nX = a:b\n")] == []


def test_discover_targets_ignores_space_indented_recipe_lines(discover) -> None:
    text = "bun:\n  curl -fsSL https://bun.sh/install | bash\nnext:\n"
    assert [task.name for task in discover(text)] == ["bun", "next"]


def test_discover_targets_recovers_rules_nested_in_error_nodes(discover) -> None:
    text = "ifeq ($(A),b)\nifdef X\nfmt: dep\n\ttrue\nendif \nendif \ntest: dep\n"
    assert [task.name for task in discover(text)] == ["fmt", "test"]


def test_discover_targets_strips_bom_and_accepts_form_feed(discover) -> None:
    text = "\ufeffall: ## Build everything\n\t@echo all\ntest: ## Run tests\n"
    assert [task.name for task in discover(text)] == ["all", "test"]
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"all": "Build everything", "test": "Run tests"}


def test_descriptions_come_from_the_header_comment_only(discover) -> None:
    text = "a: $(subst ##,,x) ## real\nb: ; @echo '## nope'\nc:\n## note\nd:\n"
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"a": "real"}


def test_discover_dedupes_and_keeps_first_description(tmp_path) -> None:
    (tmp_path / "Makefile").write_text(
        "ifdef A\nclean: ## First\nelse\nclean: ## Second\nendif\n"
    )
    tasks = MakeProvider().discover(tmp_path)
    assert [(t.name, t.description) for t in tasks] == [("clean", "First")]


@pytest.mark.parametrize("prefix", ["export", "override", "private", "unexport"])
def test_fallback_preserves_descriptions_in_sibling_comments(
    discover, prefix: str
) -> None:
    text = f"{prefix} a b: ## Build both\nlast: ## Last\n"
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {prefix: "Build both", "a": "Build both", "b": "Build both", "last": "Last"}


def test_fallback_preserves_description_after_malformed_expression(discover) -> None:
    text = "a: ## First\n$(\nb: ## Second\nc: ## Third\n"
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"a": "First", "b": "Second", "c": "Third"}


def test_fallback_does_not_take_description_from_the_next_line(discover) -> None:
    text = "export a:\n## Unrelated\nb: ## Second\n"
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"b": "Second"}


def test_fallback_ignores_continued_assignment_values(discover) -> None:
    text = "X = \\\nfake: dep\nreal: ## Real\n"
    assert [task.name for task in discover(text)] == ["real"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("before:\ndefine block\nfake: dep\n", ["before"]),
        ("before:\ndefine\nfake: dep\nendef\nafter:\n", ["before", "after"]),
        ("broken $(\ndefine X\nfake: dep\nendef\nafter:\n", ["after"]),
        (
            "broken $(\ndefine X\ndefine Y\ninner:\nendef\nouter:\nendef\nafter:\n",
            ["after"],
        ),
        (
            "before:\n  override define block\nfake: dep\nendef\nafter:\n",
            ["before", "after"],
        ),
        ("broken $(\noverride\tdefine X\nfake:\nendef\nafter:\n", ["after"]),
        ("broken $(\nexport\tdefine X\nfake:\nendef\nafter:\n", ["after"]),
    ],
)
def test_fallback_keeps_define_bodies_opaque(discover, text, expected) -> None:
    assert [task.name for task in discover(text)] == expected


def test_fallback_supports_continued_target_specific_assignments(discover) -> None:
    text = "a \\\n b: FLAGS=-x ## Build both\nreal:\n"
    assert [task.name for task in discover(text)] == ["a", "b", "real"]
    assert {
        task.name: task.description for task in discover(text) if task.description
    } == {"a": "Build both", "b": "Build both"}


@pytest.mark.parametrize("directive", ["include", "-include", "sinclude"])
def test_include_paths_with_colons_do_not_produce_targets(
    discover, directive: str
) -> None:
    assert [task.name for task in discover(f"{directive} x:y\nreal:\n")] == ["real"]


def test_descriptions_allow_metadata_after_a_plain_comment(discover) -> None:
    assert {
        task.name: task.description
        for task in discover("a: dep # explanation ## Build\n")
        if task.description
    } == {"a": "Build"}


@pytest.mark.parametrize("newline", ["\r", "\r\n", "\n"])
def test_discover_preserves_targets_and_descriptions_for_line_endings(
    tmp_path, newline: str
) -> None:
    (tmp_path / "Makefile").write_bytes(
        f"a: ## First{newline}b: ## Second{newline}".encode()
    )
    tasks = MakeProvider().discover(tmp_path)
    assert [(t.name, t.description) for t in tasks] == [("a", "First"), ("b", "Second")]
