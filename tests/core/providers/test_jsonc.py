from nur.core.providers._javascript import plain_string
from nur.core.providers._jsonc import loads_jsonc


def test_loads_plain_json() -> None:
    assert loads_jsonc('{"a": 1}', "f.json") == {"a": 1}


def test_strips_line_and_block_comments() -> None:
    text = (
        "{\n"
        "// leading comment\n"
        '"a": 1, // trailing comment\n'
        "/* block\ncomment */\n"
        '"b": [1, 2,],\n'
        "}\n"
    )
    assert loads_jsonc(text, "f.json") == {"a": 1, "b": [1, 2]}


def test_preserves_markers_inside_strings() -> None:
    text = r'{"url": "https://x.test/a,b", "q": "a\"//b", "t": [1,],}'
    assert loads_jsonc(text, "f.json") == {
        "url": "https://x.test/a,b",
        "q": 'a"//b',
        "t": [1],
    }


def test_unterminated_block_comment_returns_none(caplog) -> None:
    assert loads_jsonc('{"a": /* nope', "f.json") is None
    assert any("f.json" in r.message for r in caplog.records)


def test_malformed_json_returns_none(caplog) -> None:
    assert loads_jsonc("{ nope", "f.json") is None
    assert any("f.json" in r.message for r in caplog.records)


def test_lone_quote_returns_none(caplog) -> None:
    assert loads_jsonc('"', "f.json") is None
    assert any("f.json" in r.message for r in caplog.records)


def test_plain_string_guards() -> None:
    assert plain_string(None) is None
