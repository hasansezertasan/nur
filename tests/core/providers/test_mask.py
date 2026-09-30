import pytest

from nur.core.providers.mask import MaskProvider, parse_mask

BASIC = """\
# My Project Tasks

## build
> Compile the project

~~~sh
cargo build --release
~~~

## test (pattern) [filter]
> Run tests matching a pattern

~~~sh
cargo test $pattern
~~~

### test lint
> Subcommand: lint only
~~~bash
cargo clippy
~~~
"""


def test_parses_commands_descriptions_and_scripts() -> None:
    tasks = parse_mask(BASIC)
    assert [t.name for t in tasks] == ["build", "test", "test lint"]
    build = tasks[0]
    assert build.prefix == "mask"
    assert build.argv_base == ("mask", "build")
    assert build.description == "Compile the project"
    assert build.definition == "cargo build --release"
    assert build.source_file == "maskfile.md"


def test_heading_arguments_are_stripped_from_the_name() -> None:
    task = parse_mask(BASIC)[1]
    assert task.argv_base == ("mask", "test")
    assert task.definition == "cargo test $pattern"


def test_subcommand_runs_by_its_heading_path() -> None:
    task = parse_mask(BASIC)[2]
    assert task.argv_base == ("mask", "test", "lint")
    assert task.description == "Subcommand: lint only"


def test_subcommand_without_the_parent_prefix_keeps_its_name() -> None:
    text = "## db\n\n### migrate\n\n```sh\nalembic upgrade head\n```\n"
    tasks = parse_mask(text)
    assert [t.argv_base for t in tasks] == [("mask", "db", "migrate")]


def test_parent_prefix_is_stripped_as_a_plain_string() -> None:
    # mask strips the parent's name with a string-prefix match, so `testing`
    # under `test` becomes `ing` -- and that is the name mask's CLI accepts.
    text = "## test\n\n```sh\na\n```\n\n### testing\n\n```sh\nb\n```\n"
    assert [t.argv_base for t in parse_mask(text)] == [
        ("mask", "test"),
        ("mask", "test", "ing"),
    ]


def test_nested_subcommands_resolve_to_the_full_path() -> None:
    text = "## a\n\n### b\n\n#### c\n\n```sh\necho c\n```\n"
    tasks = parse_mask(text)
    assert [(t.name, t.argv_base) for t in tasks] == [
        ("a b c", ("mask", "a", "b", "c"))
    ]


def test_parent_without_a_script_is_not_a_task_but_its_children_are() -> None:
    text = "## services\n\n> Manage services\n\n### services up\n\n```sh\nup\n```\n"
    assert [t.name for t in parse_mask(text)] == ["services up"]


def test_heading_without_a_code_block_is_not_a_task() -> None:
    text = "# Title\n\n## Notes\n\nJust prose.\n\n## go\n\n```sh\necho go\n```\n"
    assert [t.name for t in parse_mask(text)] == ["go"]


def test_code_block_without_a_language_is_not_runnable() -> None:
    # mask picks the interpreter from the info string and refuses to run a
    # script without one.
    text = "## go\n\n```\necho go\n```\n"
    assert parse_mask(text) == []


def test_code_block_with_no_lines_is_not_runnable() -> None:
    assert parse_mask("## go\n\n```sh\n```\n") == []


def test_last_code_block_under_a_heading_wins() -> None:
    text = "## go\n\n```sh\nfirst\n```\n\n```bash\nsecond\n```\n"
    assert parse_mask(text)[0].definition == "second"


def test_windows_only_blocks_are_ignored_elsewhere() -> None:
    text = "## go\n\n```sh\necho sh\n```\n\n```powershell\nWrite-Output ps\n```\n"
    assert parse_mask(text, windows=False)[0].definition == "echo sh"
    assert parse_mask(text, windows=True)[0].definition == "Write-Output ps"


@pytest.mark.parametrize("executor", ["powershell", "batch", "cmd"])
def test_command_with_only_a_windows_block_is_skipped_elsewhere(executor) -> None:
    text = f"## go\n\n```{executor}\necho hi\n```\n"
    assert parse_mask(text, windows=False) == []
    assert [t.name for t in parse_mask(text, windows=True)] == ["go"]


def test_headings_inside_code_blocks_are_script_content() -> None:
    text = "## go\n\n```sh\n# a comment\n## not a heading\necho go\n```\n"
    tasks = parse_mask(text)
    assert [t.name for t in tasks] == ["go"]
    assert tasks[0].definition == "# a comment\n## not a heading\necho go"


def test_multiline_blockquote_is_joined() -> None:
    text = "## go\n> Does\n> things\n\n```sh\necho go\n```\n"
    assert parse_mask(text)[0].description == "Does things"


def test_command_without_a_blockquote_has_no_description() -> None:
    assert parse_mask("## go\n\nProse.\n\n```sh\necho go\n```\n")[0].description is None


def test_options_section_does_not_change_the_command() -> None:
    text = """\
## serve (port)

> Serve the site

**OPTIONS**
* verbose
    * flags: -v --verbose
    * desc: Chatty output

```sh
python -m http.server $port
```
"""
    tasks = parse_mask(text)
    assert [(t.name, t.description) for t in tasks] == [("serve", "Serve the site")]


def test_second_title_heading_ends_the_command_list() -> None:
    text = "# One\n\n## a\n\n```sh\na\n```\n\n# Two\n\n## b\n\n```sh\nb\n```\n"
    assert [t.name for t in parse_mask(text)] == ["a"]


def test_file_without_a_title_still_lists_commands() -> None:
    assert [t.name for t in parse_mask("## go\n\n```sh\necho go\n```\n")] == ["go"]


def test_closing_hashes_are_not_part_of_the_name() -> None:
    assert [t.name for t in parse_mask("## go ##\n\n```sh\ngo\n```\n")] == ["go"]


def test_empty_input_has_no_tasks() -> None:
    assert parse_mask("") == []


def test_provider_detects_and_discovers(tmp_path) -> None:
    provider = MaskProvider()
    assert not provider.detect(tmp_path)
    (tmp_path / "maskfile.md").write_text(BASIC, encoding="utf-8")
    assert provider.detect(tmp_path)
    assert [t.qualified_name for t in provider.discover(tmp_path)] == [
        "mask:build",
        "mask:test",
        "mask:test lint",
    ]


def test_provider_skips_an_undecodable_file(tmp_path, caplog) -> None:
    (tmp_path / "maskfile.md").write_bytes(b"\xff\xfe\x00")
    assert MaskProvider().discover(tmp_path) == []
    assert "skipping maskfile.md" in caplog.text


def test_commented_out_commands_are_ignored() -> None:
    text = (
        "## a\n\n```sh\nx\n```\n\n<!--\n## hidden\n\n```sh\necho\n```\n-->\n\n"
        "<!-- one line -->\n## b\n\n```sh\ny\n```\n"
    )
    assert [t.name for t in parse_mask(text)] == ["a", "b"]


def test_comment_inside_a_script_is_script_content() -> None:
    text = "## a\n\n```sh\n<!--\necho x\n```\n\n## b\n\n```sh\ny\n```\n"
    tasks = parse_mask(text)
    assert [t.name for t in tasks] == ["a", "b"]
    assert tasks[0].definition == "<!--\necho x"


def test_backticks_in_an_info_string_do_not_open_a_fence() -> None:
    text = "## a\n``` sh ```\n## b\n\n```sh\nx\n```\n"
    assert [t.name for t in parse_mask(text)] == ["b"]


def test_bare_hashes_start_an_empty_heading() -> None:
    # The script after `##` belongs to that unnamed command, not to `a`.
    assert parse_mask("## a\n##\n```sh\nx\n```\n") == []


def test_lazy_blockquote_continuation_joins_the_description() -> None:
    text = "## a\n> one\nlazy\n\n```sh\nx\n```\n"
    assert parse_mask(text)[0].description == "one lazy"


def test_duplicate_command_keeps_the_last_definition() -> None:
    text = "## a\n\n```sh\nfirst\n```\n\n## a\n\n```sh\nsecond\n```\n"
    assert [(t.name, t.definition) for t in parse_mask(text)] == [("a", "second")]


def test_tilde_fences_and_unclosed_fences_are_scripts() -> None:
    text = "## a\n\n~~~bash\nx\n~~~\n\n## b\n\n```sh\ny\n"
    assert [(t.name, t.definition) for t in parse_mask(text)] == [
        ("a", "x"),
        ("b", "y"),
    ]


def test_crlf_line_endings_are_handled() -> None:
    text = "## a\r\n> desc\r\n\r\n```sh\r\nx\r\n```\r\n"
    assert [(t.name, t.description, t.definition) for t in parse_mask(text)] == [
        ("a", "desc", "x")
    ]


def test_empty_comment_closes_on_its_own_line() -> None:
    text = "## a\n<!-->\n\n```sh\nx\n```\n\n<!--->\n## b\n\n```sh\ny\n```\n"
    assert [t.name for t in parse_mask(text)] == ["a", "b"]


def test_non_breaking_space_does_not_make_a_heading() -> None:
    text = "## a\n\n```sh\nx\n```\n\n##\u00a0b\n\n```sh\ny\n```\n"
    assert [(t.name, t.definition) for t in parse_mask(text)] == [("a", "y")]


def test_script_of_blank_lines_is_runnable() -> None:
    assert [t.name for t in parse_mask("## a\n\n```sh\n\n```\n")] == ["a"]


def test_indented_fence_body_loses_the_fence_indent() -> None:
    text = "## a\n\n  ```sh\n    code\n  x\n y\n  ```\n"
    assert parse_mask(text)[0].definition == "  code\nx\ny"


def test_heading_shallower_than_its_first_sibling_is_dropped() -> None:
    # mask nests by the first subcommand's level: `### y` is shallower than the
    # `####` that opened `a`'s subcommands, so mask drops it.
    text = (
        "## a\n\n#### x\n\n```sh\nx\n```\n\n### y\n\n```sh\ny\n```\n\n"
        "#### z\n\n```sh\nz\n```\n"
    )
    assert [t.name for t in parse_mask(text)] == ["a x", "a z"]


# Each case's expected (argv, description, definition) is what mask 0.11.7's
# `--introspect` reports for the same file.
MASK_PARITY = {
    "list_item_fence": (
        (
            "## a\n\n- ```sh\n  echo a\n  ```\n\n"
            "## b\n\n```sh\necho b\n```\n\n## c\n\n```sh\necho c\n```\n"
        ),
        [
            (("mask", "a"), None, "echo a"),
            (("mask", "b"), None, "echo b"),
            (("mask", "c"), None, "echo c"),
        ],
    ),
    "list_item_fence_ends_with_item": (
        "## a\n\n- ```sh\n  echo a\n## b\n\n```sh\necho b\n```\n",
        [(("mask", "a"), None, "echo a"), (("mask", "b"), None, "echo b")],
    ),
    "indented_after_fence": (
        "## a\n\n```sh\necho hi\n```\n\n    mask a\n\n## b\n\n```sh\necho b\n```\n",
        [(("mask", "b"), None, "echo b")],
    ),
    "indented_in_list": (
        "## a\n\n- item\n\n    continued\n\n```sh\necho a\n```\n",
        [(("mask", "a"), None, "echo a")],
    ),
    "indented_para_continuation": (
        "## a\n\ntext\n    more text\n\n```sh\necho a\n```\n",
        [(("mask", "a"), None, "echo a")],
    ),
    "details_block": (
        "## a\n\n```sh\necho a\n```\n\n<details>\n## b\n\n```sh\necho b\n```\n",
        [(("mask", "a"), None, "echo b")],
    ),
    "div_hides_fence": ("## a\n\n<div>\n```sh\necho hidden\n```\n</div>\n", []),
    "lone_tag": ('# T\n<img src="x">\n## a\n\n```sh\necho\n```\n', []),
    "lone_tag_after_paragraph": (
        '## a\ntext\n<img src="x">\n```sh\necho\n```\n',
        [(("mask", "a"), None, "echo")],
    ),
    "link_heading": (
        "## [build](docs/build.md)\n\n```sh\necho\n```\n",
        [(("mask", "build"), None, "echo")],
    ),
    "same_name_different_argv": (
        (
            "## deploy prod\n\n```sh\necho top\n```\n\n"
            "## deploy\n\n```sh\necho d\n```\n\n### prod\n\n```sh\necho sub\n```\n"
        ),
        [
            (("mask", "deploy prod"), None, "echo top"),
            (("mask", "deploy"), None, "echo d"),
            (("mask", "deploy", "prod"), None, "echo sub"),
        ],
    ),
    "form_feed": (
        "## a\n\n```sh\necho\x0c## x\n```\n",
        [(("mask", "a"), None, "echo\x0c## x")],
    ),
    "line_separator": ("text\u2028## a\n\n```sh\necho\n```\n", []),
    "setext_h2": (
        "build\n-----\n\n```sh\necho b\n```\n",
        [(("mask", "build"), None, "echo b")],
    ),
    "setext_h1_ends": (
        "## a\n\n```sh\nx\n```\n\nTwo\n===\n\n## b\n\n```sh\ny\n```\n",
        [(("mask", "a"), None, "x")],
    ),
    "thematic_not_setext": (
        "## a\n\n---\n\n```sh\nx\n```\n",
        [(("mask", "a"), None, "x")],
    ),
    "list_then_dash": (
        "## a\n- item\n---\n\n```sh\nx\n```\n",
        [(("mask", "a"), None, "x")],
    ),
    "quote_list_interrupt": (
        "## a\n> Build it\n- flag\n\n```sh\nx\n```\n",
        [(("mask", "a"), "Build it", "x")],
    ),
    "quote_thematic": (
        "## a\n> Build it\n---\n\n```sh\nx\n```\n",
        [(("mask", "a"), "Build it", "x")],
    ),
    "quote_last_paragraph": (
        "## a\n> first\n>\n> second\n\n```sh\nx\n```\n",
        [(("mask", "a"), "second", "x")],
    ),
    "quote_trailing_empty": (
        "## a\n> desc\n>\n\n```sh\nx\n```\n",
        [(("mask", "a"), "desc", "x")],
    ),
    "link_after_text": (
        "## x [a](u) y\n\n```sh\necho\n```\n",
        [(("mask", "a y"), None, "echo")],
    ),
    "image_heading": (
        "## x ![img](u)\n\n```sh\necho\n```\n",
        [(("mask", "img"), None, "echo")],
    ),
    "two_links": (
        "## x [a](u) y [b](v)\n\n```sh\necho\n```\n",
        [(("mask", "b"), None, "echo")],
    ),
    "escape_heading": (
        "## a\\_b\n\n```sh\necho\n```\n",
        [(("mask", "a_b"), None, "echo")],
    ),
    "entity_heading": (
        "## a &amp; b\n\n```sh\necho\n```\n",
        [(("mask", "a & b"), None, "echo")],
    ),
    "lone_tag_after_thematic": (
        '## a\n\n```sh\nx\n```\n---\n<img src="x">\n## b\n\n```sh\ny\n```\n',
        [(("mask", "a"), None, "y")],
    ),
    "lone_tag_after_setext": (
        "## a\n\n```sh\nx\n```\nfoo\n---\n<br>\n## b\n\n```sh\ny\n```\n",
        [(("mask", "a"), None, "x"), (("mask", "foo"), None, "y")],
    ),
    "lone_tag_after_empty_quote": (
        "## a\n\n```sh\nx\n```\n>\n<br>\n## b\n\n```sh\ny\n```\n",
        [(("mask", "a"), None, "y")],
    ),
    "list_fence_then_indented": (
        "## a\n\n- ```sh\n  echo\n  ```\n    cont\n",
        [(("mask", "a"), None, "echo")],
    ),
    "indented_code_in_list": ("## a\n\n```sh\necho\n```\n\n- x\n\n      code\n", []),
    "heading_in_list_item": (
        "## a\n\n```sh\nx\n```\n\n- ## b\n\n```sh\ny\n```\n",
        [(("mask", "a"), None, "x"), (("mask", "b"), None, "y")],
    ),
    "title_in_quote_stops": (
        "## a\n\n```sh\nx\n```\n\n> # T2\n\n## b\n\n```sh\ny\n```\n",
        [(("mask", "a"), None, "x")],
    ),
    "pre_closed_by_script": (
        "## a\n\n```sh\nx\n```\n\n<pre>\n</script>\n### c\n```js\ny\n```\n",
        [(("mask", "a"), None, "x")],
    ),
    "lone_cr": ("# T\r## a\r```sh\recho\r```\r", []),
}


@pytest.mark.parametrize(
    ("text", "expected"), list(MASK_PARITY.values()), ids=list(MASK_PARITY)
)
def test_matches_mask_on_block_structure(text, expected) -> None:
    tasks = parse_mask(text, windows=False)
    assert [(t.argv_base, t.description, t.definition) for t in tasks] == expected


def test_hostile_bracket_heading_is_parsed() -> None:
    # A run of unclosed `[` must not make link matching backtrack
    # quadratically; mask names the command up to the first bracket.
    text = "## " + "[" * 50_000 + "\n\n```sh\necho\n```\n"
    assert parse_mask(text) == []
