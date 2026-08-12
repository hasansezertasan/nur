from pathlib import Path

from nur.core.providers.xc import XcProvider, parse_xc

REPO_README = Path(__file__).resolve().parents[3] / "README.md"

BASIC = """\
# Project

Some prose.

## Tasks

### build

```sh
uv build
```
"""


MARKER = """\
# Project

## Usage

### TUI

## Development

<!-- xc-heading -->
## Contributing

### style

```sh
tox run -e style
```

## Releasing

### not-a-task

```sh
echo no
```
"""


def test_parses_task_under_tasks_heading() -> None:
    tasks = parse_xc(BASIC)
    assert [t.name for t in tasks] == ["build"]
    task = tasks[0]
    assert task.prefix == "xc"
    assert task.argv_base == ("xc", "build")
    assert task.definition == "uv build"
    assert task.source_file == "README.md"


def test_xc_heading_marker_selects_a_differently_named_heading() -> None:
    assert [t.name for t in parse_xc(MARKER)] == ["style"]


def test_marker_wins_over_a_later_tasks_heading() -> None:
    text = MARKER + "\n## Tasks\n\n### ignored\n\n```sh\necho no\n```\n"
    assert [t.name for t in parse_xc(text)] == ["style"]


def test_marker_wins_over_an_earlier_tasks_heading() -> None:
    text = (
        "## Tasks\n\n### ignored\n\n```sh\necho no\n```\n\n"
        "<!-- xc-heading -->\n## Dev\n\n### style\n\n```sh\ntox\n```\n"
    )
    assert [t.name for t in parse_xc(text)] == ["style"]


def test_marker_tolerates_blank_lines_before_the_heading() -> None:
    text = "<!-- xc-heading -->\n\n\n## Dev\n\n### go\n\n```sh\necho hi\n```\n"
    assert [t.name for t in parse_xc(text)] == ["go"]


HASHES_IN_SCRIPT = """\
## Tasks

### build

```sh
# install first
### deep comment
uv build
```

### test

```sh
pytest
```
"""


def test_hash_lines_inside_a_script_are_not_headings() -> None:
    tasks = parse_xc(HASHES_IN_SCRIPT)
    assert [t.name for t in tasks] == ["build", "test"]
    assert tasks[0].definition == "# install first\n### deep comment\nuv build"


def test_marker_inside_a_script_is_ignored() -> None:
    # A README documenting xc quotes the marker in a fenced example; that must
    # not hijack task discovery.
    text = (
        "# Docs\n\n````md\n<!-- xc-heading -->\n## Fake\n\n### fake-task\n````\n\n"
        "## Tasks\n\n### real\n\n```sh\necho real\n```\n"
    )
    assert [t.name for t in parse_xc(text)] == ["real"]


def test_tilde_fences_are_recognised() -> None:
    text = (
        "## Tasks\n\n### build\n\n~~~sh\nuv build\n~~~\n\n"
        "### test\n\n~~~\npytest\n~~~\n"
    )
    tasks = parse_xc(text)
    assert [t.name for t in tasks] == ["build", "test"]
    assert tasks[0].definition == "uv build"


def test_longer_fence_wraps_shorter_one() -> None:
    text = "## Tasks\n\n### demo\n\n````\n```\necho hi\n```\n````\n"
    assert parse_xc(text)[0].definition == "```\necho hi\n```"


def test_unclosed_fence_runs_to_the_end_of_the_section() -> None:
    text = "## Tasks\n\n### build\n\n```sh\nuv build\n"
    assert parse_xc(text)[0].definition == "uv build"


def test_indented_example_is_a_code_block_not_a_task_list() -> None:
    # Four spaces of indentation makes an indented code block, so xc's markdown
    # parser sees no headings at all -- nur must not offer tasks xc cannot run.
    text = (
        "# Project\n\nExample:\n\n    ## Tasks\n\n    ### fake\n\n"
        "    ```sh\n    echo no\n    ```\n"
    )
    assert parse_xc(text) == []


def test_indented_heading_inside_a_task_body_is_not_a_task() -> None:
    text = "## Tasks\n\n### build\n\n    ### indented-example\n\n```sh\nuv build\n```\n"
    assert [t.name for t in parse_xc(text)] == ["build"]


def test_heading_may_carry_up_to_three_leading_spaces() -> None:
    # CommonMark allows an ATX heading to be indented by up to three spaces.
    text = "  ## Tasks\n\n   ### build\n\n```sh\nuv build\n```\n"
    assert [t.name for t in parse_xc(text)] == ["build"]


def test_fence_delimiter_with_trailing_text_does_not_close_a_block() -> None:
    # A closing fence may only be followed by whitespace, so this line is
    # script content. Treating it as a close truncates `build` and swallows the
    # task that follows.
    text = (
        "## Tasks\n\n### build\n\n```sh\n```not-a-close\nuv build\n```\n\n"
        "### test\n\n```sh\npytest\n```\n"
    )
    tasks = parse_xc(text)
    assert [t.name for t in tasks] == ["build", "test"]
    assert tasks[0].definition == "```not-a-close\nuv build"
    assert tasks[1].definition == "pytest"


def test_indented_fence_still_opens_and_closes() -> None:
    text = "## Tasks\n\n### build\n\n  ```sh\n  uv build\n  ```\n"
    assert parse_xc(text)[0].definition == "  uv build"


def test_marker_does_not_carry_across_a_fenced_block() -> None:
    # Only blank lines may sit between a marker and its heading; a fenced block
    # breaks that adjacency. With no `Tasks` fallback either, nothing matches.
    text = (
        "<!-- xc-heading -->\n\n```sh\necho x\n```\n\n"
        "## Dev\n\n### go\n\n```sh\nhi\n```\n"
    )
    assert parse_xc(text) == []


def test_indented_code_block_in_body_is_not_description() -> None:
    # A four-space-indented block is code, not prose, so it must not surface as
    # the task's description.
    text = "## Tasks\n\n### build\n\n    indented sample\n\n```sh\nuv build\n```\n"
    assert parse_xc(text)[0].description is None


ATTRIBUTES = """\
## Tasks

### deploy

Ship the built artifact
to production.

requires: test, lint
Directory: ./deployment
Env: ENVIRONMENT=STAGING
Inputs: VERSION
run: once

```sh
sh deploy.sh
```
"""


def test_prose_becomes_the_description_without_attribute_lines() -> None:
    task = parse_xc(ATTRIBUTES)[0]
    assert task.description == "Ship the built artifact to production."
    assert task.definition == "sh deploy.sh"


def test_description_is_none_when_only_attributes_precede_the_script() -> None:
    text = "## Tasks\n\n### deploy\n\nreq: test\n\n```sh\nsh deploy.sh\n```\n"
    assert parse_xc(text)[0].description is None


def test_prose_after_the_script_is_not_part_of_the_description() -> None:
    text = "## Tasks\n\n### build\n\n```sh\nuv build\n```\n\nTrailing note.\n"
    assert parse_xc(text)[0].description is None


def test_heading_with_whitespace_is_not_a_task() -> None:
    # xc forbids whitespace in task names, so such a heading is prose.
    text = "## Tasks\n\n### Build the docs\n\n```sh\nmake docs\n```\n\n### docs\n"
    assert [t.name for t in parse_xc(text)] == ["docs"]


def test_code_span_and_emphasis_are_stripped_from_names() -> None:
    # xc reads heading text from the markdown AST, so `### `build`` is `build`.
    # copier-pyproject writes every task heading as an inline code span.
    text = "## Tasks\n\n### `build`\n\n```sh\nuv build\n```\n\n### **test**\n"
    assert [t.name for t in parse_xc(text)] == ["build", "test"]


def test_emphasised_tasks_heading_is_recognised() -> None:
    text = "## **Tasks**\n\n### build\n\n```sh\nuv build\n```\n"
    assert [t.name for t in parse_xc(text)] == ["build"]


def test_task_without_a_script_is_still_discovered() -> None:
    # A `requires:`-only task exists to chain dependencies; xc can run it.
    text = "## Tasks\n\n### all\n\nrequires: build, test\n"
    task = parse_xc(text)[0]
    assert task.name == "all"
    assert task.definition == ""


def test_nested_heading_is_body_not_a_task() -> None:
    text = "## Tasks\n\n### build\n\n#### Notes\n\n```sh\nuv build\n```\n"
    tasks = parse_xc(text)
    assert [t.name for t in tasks] == ["build"]
    assert tasks[0].definition == "uv build"


def test_section_ends_at_the_next_same_level_heading() -> None:
    text = "## Tasks\n\n### build\n\n```sh\nuv build\n```\n\n## License\n\n### mit\n"
    assert [t.name for t in parse_xc(text)] == ["build"]


def test_only_the_first_tasks_section_is_read() -> None:
    text = (
        "## Tasks\n\n### build\n\n```sh\nuv build\n```\n\n"
        "# Other\n\n## Tasks\n\n### ignored\n\n```sh\necho no\n```\n"
    )
    assert [t.name for t in parse_xc(text)] == ["build"]


def test_no_task_section_yields_nothing() -> None:
    assert parse_xc("# Project\n\nJust a readme.\n") == []


def test_detect_true(tmp_path) -> None:
    (tmp_path / "README.md").write_text(BASIC)
    assert XcProvider().detect(tmp_path)


def test_detect_false_without_readme(tmp_path) -> None:
    assert not XcProvider().detect(tmp_path)


def test_detect_false_when_readme_has_no_tasks(tmp_path) -> None:
    # Nearly every repository has a README, so its presence proves nothing.
    (tmp_path / "README.md").write_text("# Project\n\nNo tasks here.\n")
    assert not XcProvider().detect(tmp_path)


def test_discover(tmp_path) -> None:
    (tmp_path / "README.md").write_text(BASIC)
    tasks = XcProvider().discover(tmp_path)
    assert [t.qualified_name for t in tasks] == ["xc:build"]
    assert tasks[0].source_file == "README.md"


def test_discover_without_readme_returns_empty(tmp_path) -> None:
    assert XcProvider().discover(tmp_path) == []


def test_discover_non_utf8_returns_empty(tmp_path, caplog) -> None:
    # 0xff is never a valid UTF-8 byte, so read_text raises UnicodeDecodeError.
    (tmp_path / "README.md").write_bytes(b"## Tasks\n\n### x\n\n```\n\xff\n```\n")
    assert XcProvider().discover(tmp_path) == []
    assert any("README.md" in r.message for r in caplog.records)


def test_extra_args_are_passed_positionally(tmp_path) -> None:
    # xc takes task inputs as positional args: `xc greet Joe` -- no `--`.
    (tmp_path / "README.md").write_text(BASIC)
    task = XcProvider().discover(tmp_path)[0]
    assert task.run_argv(["--watch"]) == ["xc", "build", "--watch"]


def test_this_repository_readme_parses_to_its_documented_tasks() -> None:
    # The real-world case, and it pins the section boundaries in both
    # directions: the `### TUI`/`### CLI`/`### Debugging` headings under
    # `## Usage` come before the marker, and the section ends at `## Releasing`.
    tasks = parse_xc(REPO_README.read_text(encoding="utf-8"))
    assert [t.name for t in tasks] == [
        "install",
        "style",
        "ci",
        "docs-build",
        "docs-server",
        "docs-linkcheck",
    ]
    assert tasks[0].definition == "uv sync"
    assert tasks[0].description == "Install the dependencies:"
