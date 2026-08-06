from typing import TYPE_CHECKING

from nur.providers.deno import DenoProvider

if TYPE_CHECKING:
    from pathlib import Path

JSON = """
{
  "tasks": {
    "dev": "deno run --watch main.ts",
    "build": {
      "command": "deno compile main.ts",
      "description": "Compile the binary"
    }
  }
}
"""

JSONC = """
{
  // dev server
  "tasks": {
    "dev": "deno run --watch main.ts", // watch mode
    /* build step */
    "build": "deno compile main.ts",
  },
}
"""


def _write(tmp_path: Path, text: str, name: str = "deno.json") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return tmp_path


def test_detect_true(tmp_path) -> None:
    _write(tmp_path, JSON)
    assert DenoProvider().detect(tmp_path)


def test_detect_false_without_tasks(tmp_path) -> None:
    _write(tmp_path, '{"name": "x"}')
    assert not DenoProvider().detect(tmp_path)


def test_detect_false_without_file(tmp_path) -> None:
    assert not DenoProvider().detect(tmp_path)


def test_detect_false_on_empty_tasks(tmp_path) -> None:
    _write(tmp_path, '{"tasks": {}}')
    assert not DenoProvider().detect(tmp_path)


def test_discover_string_task(tmp_path) -> None:
    _write(tmp_path, JSON)
    tasks = {t.name: t for t in DenoProvider().discover(tmp_path)}
    assert tasks["dev"].argv_base == ("deno", "task", "dev")
    assert tasks["dev"].definition == "deno run --watch main.ts"
    assert tasks["dev"].description is None
    assert tasks["dev"].source_file == "deno.json"
    assert tasks["dev"].prefix == "deno"


def test_discover_object_task(tmp_path) -> None:
    _write(tmp_path, JSON)
    tasks = {t.name: t for t in DenoProvider().discover(tmp_path)}
    assert tasks["build"].definition == "deno compile main.ts"
    assert tasks["build"].description == "Compile the binary"


def test_jsonc_comments_and_trailing_commas(tmp_path) -> None:
    _write(tmp_path, JSONC, name="deno.jsonc")
    tasks = {t.name: t for t in DenoProvider().discover(tmp_path)}
    assert tasks["dev"].definition == "deno run --watch main.ts"
    assert tasks["build"].definition == "deno compile main.ts"
    assert tasks["build"].source_file == "deno.jsonc"


def test_deno_json_wins_over_jsonc(tmp_path) -> None:
    _write(tmp_path, '{"tasks": {"a": "echo json"}}', name="deno.json")
    _write(tmp_path, '{"tasks": {"b": "echo jsonc"}}', name="deno.jsonc")
    names = {t.name for t in DenoProvider().discover(tmp_path)}
    assert names == {"a"}


def test_comment_marker_inside_string_is_preserved(tmp_path) -> None:
    _write(tmp_path, '{"tasks": {"url": "curl https://x.dev // y"}}')
    tasks = {t.name: t for t in DenoProvider().discover(tmp_path)}
    assert tasks["url"].definition == "curl https://x.dev // y"


def test_discover_empty_without_file(tmp_path) -> None:
    assert DenoProvider().discover(tmp_path) == []


def test_absent_file_emits_no_warning(tmp_path, caplog) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="nur"):
        assert not DenoProvider().detect(tmp_path)
        assert DenoProvider().discover(tmp_path) == []
    assert caplog.records == []


def test_malformed_json_returns_empty(tmp_path) -> None:
    _write(tmp_path, '{"tasks": {')
    assert DenoProvider().discover(tmp_path) == []
