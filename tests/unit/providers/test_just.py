from typing import Never

from nur.providers.just import JustProvider, parse_dump

DUMP = """
{
  "recipes": {
    "build": {"name": "build", "doc": "Build the project"},
    "test": {"name": "test", "doc": null}
  },
  "settings": {}
}
"""


def test_parse_dump() -> None:
    tasks = {t.name: t for t in parse_dump(DUMP)}
    assert tasks["build"].argv_base == ("just", "build")
    assert tasks["build"].description == "Build the project"
    assert tasks["build"].prefix == "just"
    assert tasks["test"].description is None


def test_detect(tmp_path) -> None:
    (tmp_path / "justfile").write_text("build:\n  echo hi\n")
    assert JustProvider().detect(tmp_path)
    assert not JustProvider().detect(tmp_path / "nope")


def test_discover_tool_missing_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "justfile").write_text("build:\n  echo hi\n")

    def boom(*a, **k) -> Never:
        msg = "just"
        raise FileNotFoundError(msg)

    monkeypatch.setattr("nur.providers.just.subprocess.run", boom)
    assert JustProvider().discover(tmp_path) == []
    assert any("just" in r.message for r in caplog.records)


def test_discover_uses_dump(tmp_path, monkeypatch) -> None:
    (tmp_path / "justfile").write_text("build:\n  echo hi\n")

    class FakeProc:
        stdout = DUMP

    monkeypatch.setattr("nur.providers.just.subprocess.run", lambda *a, **k: FakeProc())
    names = {t.name for t in JustProvider().discover(tmp_path)}
    assert names == {"build", "test"}


def test_discover_malformed_json_returns_empty(tmp_path, monkeypatch, caplog) -> None:
    (tmp_path / "justfile").write_text("build:\n  echo hi\n")

    class FakeProc:
        stdout = "invalid json"

    monkeypatch.setattr("nur.providers.just.subprocess.run", lambda *a, **k: FakeProc())
    assert JustProvider().discover(tmp_path) == []
    assert any("skipping justfile" in r.message for r in caplog.records)


def test_parse_dump_honors_source_file() -> None:
    # `discover()` passes the detected filename through; on case-insensitive
    # filesystems the capital-vs-lowercase variant can't be distinguished, so
    # verify the parameterization directly on the pure function.
    tasks = parse_dump(DUMP, source_file="Justfile")
    assert tasks
    assert all(t.source_file == "Justfile" for t in tasks)
