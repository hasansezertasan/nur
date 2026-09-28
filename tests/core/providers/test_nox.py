import warnings
from typing import TYPE_CHECKING

from nur.core.providers.nox import NoxProvider, parse_noxfile

if TYPE_CHECKING:
    from pathlib import Path

NOXFILE = '''
import nox


@nox.session
def tests(session):
    """Run the test suite.

    Longer explanation that is not the description.
    """
    session.run("pytest")


@nox.session(python=["3.13", "3.14"])
@nox.parametrize("django", ["4.2", "5.0"])
def lint(session, django):
    session.run("ruff", "check")


@nox.session(name="docs-build", tags=["docs"])
def docs(session):
    session.run("sphinx-build")


def helper(session):
    session.run("echo")
'''


def _names(text: str) -> list[str]:
    return [t.name for t in parse_noxfile(text)]


def test_detect(tmp_path: Path) -> None:
    assert not NoxProvider().detect(tmp_path)
    (tmp_path / "noxfile.py").write_text(NOXFILE, encoding="utf-8")
    assert NoxProvider().detect(tmp_path)


def test_discover(tmp_path: Path) -> None:
    (tmp_path / "noxfile.py").write_text(NOXFILE, encoding="utf-8")
    tasks = {t.name: t for t in NoxProvider().discover(tmp_path)}
    assert list(tasks) == ["tests", "lint", "docs-build"]
    assert tasks["tests"].argv_base == ("nox", "-s", "tests")
    assert tasks["tests"].description == "Run the test suite."
    assert tasks["tests"].definition == ""
    assert tasks["tests"].source_file == "noxfile.py"
    assert tasks["lint"].description is None
    assert tasks["docs-build"].argv_base == ("nox", "-s", "docs-build")


def test_extra_args_become_session_posargs() -> None:
    (task,) = parse_noxfile("import nox\n@nox.session\ndef t(session): ...\n")
    assert task.run_argv(["-k", "foo"]) == ["nox", "-s", "t", "--", "-k", "foo"]
    assert task.run_argv() == ["nox", "-s", "t"]


def test_import_aliases() -> None:
    text = (
        "import nox as n\n"
        "from nox import session\n"
        "from nox import session as s\n"
        "@n.session\ndef a(session): ...\n"
        "@session\ndef b(session): ...\n"
        "@s(python='3.14')\ndef c(session): ...\n"
    )
    assert _names(text) == ["a", "b", "c"]


def test_ignores_unrelated_decorators() -> None:
    text = (
        "import nox\nimport other\nfrom other import session\n"
        "@other.session\ndef a(s): ...\n"
        "@session\ndef b(s): ...\n"
        "@nox.parametrize('x', [1])\ndef c(s): ...\n"
    )
    assert _names(text) == []


def test_requires_nox_import() -> None:
    # Without a nox import, `nox.session` refers to nothing nox registers.
    assert _names("@nox.session\ndef a(session): ...\n") == []
    assert _names("@session\ndef a(session): ...\n") == []


def test_explicit_name_handling() -> None:
    # nox registers `name or func.__name__`, so None and "" fall back.
    text = (
        "import nox\nNAME = 'x'\nKW = {'name': 'y'}\n"
        "@nox.session(name=NAME)\ndef computed(session): ...\n"
        "@nox.session(**KW)\ndef splatted(session): ...\n"
        "@nox.session(name=None)\ndef default(session): ...\n"
        "@nox.session(name='')\ndef empty(session): ...\n"
        "@nox.session()\ndef bare_call(session): ...\n"
    )
    assert _names(text) == ["default", "empty", "bare_call"]


def test_only_unconditional_blocks_are_searched() -> None:
    text = (
        "import nox\nimport sys\nimport contextlib\n"
        "if sys.platform == 'linux':\n"
        "    @nox.session\n    def linux(session): ...\n"
        "else:\n"
        "    @nox.session\n    def other(session): ...\n"
        "try:\n"
        "    @nox.session\n    def in_try(session): ...\n"
        "except ImportError:\n"
        "    @nox.session\n    def in_except(session): ...\n"
        "else:\n"
        "    @nox.session\n    def in_else(session): ...\n"
        "finally:\n"
        "    @nox.session\n    def in_finally(session): ...\n"
        "with contextlib.suppress(Exception):\n"
        "    @nox.session\n    def in_with(session): ...\n"
        "for py in ['3.13', '3.14']:\n"
        "    @nox.session(python=py)\n    def in_loop(session): ...\n"
        "match sys.platform:\n"
        "    case 'linux':\n"
        "        @nox.session\n        def in_match(session): ...\n"
        "if __name__ == '__main__':\n"
        "    @nox.session\n    def never(session): ...\n"
        "def factory():\n"
        "    @nox.session\n    def dynamic(session): ...\n"
        "class C:\n"
        "    @nox.session\n    def in_class(session): ...\n"
    )
    # try/else/with bodies can be cut short by a handled exception.
    assert _names(text) == ["in_finally", "in_class"]


def test_session_after_caught_exception_is_not_listed() -> None:
    text = (
        "import nox\n"
        "try:\n    import sphinx\n"
        "    @nox.session\n    def docs(session): ...\n"
        "except ImportError:\n    pass\n"
    )
    assert _names(text) == []


def test_bindings_follow_statement_order() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names(session + "import nox\n") == []
    assert _names("import nox\nnox = object()\n" + session) == []
    assert _names("import nox\ndef nox(): ...\n" + session) == []
    assert _names("import nox\n(nox := 1)\n" + session) == []
    assert _names("import nox\nimport other as nox\n" + session) == []
    assert _names("import nox\nfor nox in []:\n    pass\n" + session) == []
    assert _names("import nox\nfrom helpers import *\n" + session) == []
    text = (
        "import nox\n" + session + "del nox\nimport nox\n" + session.replace("s(", "t(")
    )
    assert _names(text) == ["s", "t"]


def test_rebinding_on_any_path_invalidates() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nif x:\n    nox = 1\n" + session) == []
    assert _names("import nox\nwhile x:\n    nox = 1\n" + session) == []
    assert (
        _names(
            "import nox\ntry:\n    pass\nexcept Exception as nox:\n    pass\n" + session
        )
        == []
    )
    assert (
        _names(
            "import nox\nimport sys\nmatch sys.platform:\n    case nox:\n        pass\n"
            + session
        )
        == []
    )


def test_decorators_see_bindings_from_those_above() -> None:
    text = (
        "import nox\nimport other\n"
        "@((nox := other).session)\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_global_declarations_make_a_name_untrackable() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    in_class = "import nox\nclass C:\n    global nox\n    import other as nox\n"
    assert _names(in_class + session) == []
    in_function = "import nox\ndef setup():\n    global nox\n    nox = 1\nsetup()\n"
    assert _names(in_function + session) == []


def test_file_that_cannot_compile_lists_nothing(tmp_path: Path, caplog) -> None:
    session = "import nox\n@nox.session\ndef s(session): ...\n"
    for broken in (
        "@nox.session(name='a', name='b')\ndef t(session): ...\n",
        "return\n",
    ):
        (tmp_path / "noxfile.py").write_text(session + broken, encoding="utf-8")
        assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)


def test_replacing_the_session_attribute_invalidates_modules() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nnox.session = print\n" + session) == []
    assert _names("import nox\ndel nox.session\n" + session) == []
    assert _names("import nox\nimport nox as n\nn.session = print\n" + session) == []
    assert _names("import nox\nsetattr(nox, 'session', print)\n" + session) == []
    in_class = "import nox\nclass C:\n    nox.session = print\n"
    assert _names(in_class + session) == []
    reimport = "import nox\nnox.session = print\nimport nox\n"
    assert _names(reimport + session) == []
    # Ordinary nox configuration leaves the decorator alone.
    text = "import nox\nnox.needs_version = '>=2024'\nnox.options.sessions = []\n"
    assert _names(text + session) == ["s"]


def test_unconditional_raise_ends_the_path() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\n" + session + "raise RuntimeError\n") == []
    assert _names("import nox\nclass C:\n    raise RuntimeError\n" + session) == []
    guarded = "import nox\nif x:\n    raise RuntimeError\n" + session
    assert _names(guarded) == ["s"]
    handled = (
        "import nox\ntry:\n    raise RuntimeError\n"
        "except RuntimeError:\n    pass\n" + session
    )
    assert _names(handled) == ["s"]


def test_raise_must_be_caught_by_a_handler() -> None:
    session = "@nox.session\ndef s(session): ...\n"

    def guarded(raised: str, caught: str) -> list[str]:
        handler = f"except {caught}:" if caught else "except:"
        text = f"import nox\ntry:\n    raise {raised}\n{handler}\n    pass\n"
        return _names(text + session)

    assert guarded("TypeError", "ValueError") == []
    assert guarded("err", "ValueError") == []
    assert guarded("SystemExit", "Exception") == []
    assert guarded("TypeError('x')", "TypeError") == ["s"]
    assert guarded("TypeError", "(ValueError, TypeError)") == ["s"]
    assert guarded("TypeError", "Exception") == ["s"]
    assert guarded("err", "BaseException") == ["s"]
    assert guarded("err", "") == ["s"]


def test_except_target_is_deleted_after_the_handler() -> None:
    text = (
        "import nox\ntry:\n    pass\n"
        "except ImportError as nox:\n    import nox\n"
        "@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_branches_must_agree_on_the_binding() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    text = "if x:\n    import nox\nelse:\n    import other as nox\n" + session
    assert _names(text) == []


def test_nox_uv_is_a_drop_in_replacement() -> None:
    assert _names(
        "from nox_uv import session\n@session(name='a')\ndef b(s): ...\n"
    ) == ["a"]
    assert _names("import nox_uv\n@nox_uv.session\ndef c(s): ...\n") == ["c"]


def test_fallback_import_in_except_is_recognised() -> None:
    text = (
        "try:\n    from nox_uv import session\n"
        "except ImportError:\n    from nox import session\n"
        "@session\ndef tests(s): ...\n"
    )
    assert _names(text) == ["tests"]


def test_import_must_be_bound_on_every_path() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import sys\nif sys.flags:\n    import nox\n" + session) == []
    assert _names("for _ in []:\n    import nox\n" + session) == []
    assert (
        _names("try:\n    import nox\nexcept ImportError:\n    pass\n" + session) == []
    )
    assert _names(
        "import sys\nif sys.flags:\n    import nox\nelse:\n    import nox as nox\n"
        + session
    ) == ["s"]
    assert _names("try:\n    pass\nfinally:\n    import nox\n" + session) == ["s"]


def test_long_elif_chain_is_skipped_not_crashing(tmp_path: Path, caplog) -> None:
    chain = "".join(f"elif x == {i}:\n    pass\n" for i in range(1, 5000))
    (tmp_path / "noxfile.py").write_text(
        "import nox\nx = 0\nif x == 0:\n    pass\n" + chain, encoding="utf-8"
    )
    assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)


def test_import_inside_class_body_is_not_module_level() -> None:
    text = "class C:\n    import nox\n@nox.session\ndef outer(session): ...\n"
    assert _names(text) == []


def test_mapping_splat_skips_regardless_of_keyword_order() -> None:
    text = (
        "import nox\nKW = {}\n"
        "@nox.session(name='a', **KW)\ndef a(session): ...\n"
        "@nox.session(**KW, name='b')\ndef b(session): ...\n"
    )
    assert _names(text) == []


def test_stacked_session_decorators_register_aliases() -> None:
    text = (
        "import nox\nNAME = 'x'\n"
        "@nox.session(name='test')\n@nox.session(name=NAME)\n@nox.session(name='tests')\n"
        "def tests(session): ...\n"
    )
    assert _names(text) == ["tests", "test"]


def test_whitespace_only_docstring_has_no_description() -> None:
    (task,) = parse_noxfile(
        'import nox\n@nox.session\ndef t(s):\n    """\n   \n    """\n'
    )
    assert task.description is None


def test_syntax_warnings_are_not_emitted() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _names('import nox\nPAT = "\\d+"\n@nox.session\ndef t(s): ...\n') == [
            "t"
        ]


def test_star_and_submodule_imports() -> None:
    assert _names("from nox import *\n@session\ndef a(session): ...\n") == ["a"]
    text = "import nox.registry\n@nox.session\ndef b(session): ...\n"
    assert _names(text) == ["b"]


def test_shared_explicit_name_keeps_one_session() -> None:
    text = (
        "import nox\n"
        "@nox.session(name='x')\ndef a(session):\n    '''first'''\n"
        "@nox.session(name='x')\ndef b(session):\n    '''second'''\n"
    )
    tasks = parse_noxfile(text)
    assert [(t.name, t.description) for t in tasks] == [("x", "second")]


def test_redefinition_keeps_first_position_and_last_definition() -> None:
    text = (
        "import nox\n"
        "@nox.session\ndef a(session):\n    '''old'''\n"
        "@nox.session\ndef b(session): ...\n"
        "@nox.session\ndef a(session):\n    '''new'''\n"
    )
    tasks = parse_noxfile(text)
    assert [t.name for t in tasks] == ["a", "b"]
    assert tasks[0].description == "new"


def test_discovery_never_executes_the_noxfile(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    (tmp_path / "noxfile.py").write_text(
        f"import nox, pathlib\npathlib.Path({str(marker)!r}).touch()\n"
        "@nox.session\ndef t(session): ...\n",
        encoding="utf-8",
    )
    assert [t.name for t in NoxProvider().discover(tmp_path)] == ["t"]
    assert not marker.exists()


def test_syntax_error_returns_empty(tmp_path: Path, caplog) -> None:
    (tmp_path / "noxfile.py").write_text("import nox\ndef (:\n", encoding="utf-8")
    assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)


def test_bom_is_rejected_like_nox(tmp_path: Path) -> None:
    # nox reads the noxfile as strict UTF-8 text and fails on a BOM, so listing
    # its sessions would offer tasks that cannot run.
    (tmp_path / "noxfile.py").write_bytes(
        b"\xef\xbb\xbfimport nox\n@nox.session\ndef t(session): ...\n"
    )
    assert NoxProvider().discover(tmp_path) == []


def test_undecodable_file_returns_empty(tmp_path: Path, caplog) -> None:
    (tmp_path / "noxfile.py").write_bytes(b"\xff\xfe\x00bad")
    assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)
