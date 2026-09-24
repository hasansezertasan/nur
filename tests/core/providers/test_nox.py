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
    (tmp_path / "noxfile.py").write_text(NOXFILE)
    assert NoxProvider().detect(tmp_path)


def test_discover(tmp_path: Path) -> None:
    (tmp_path / "noxfile.py").write_text(NOXFILE)
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
    assert _names(text) == ["in_try", "in_else", "in_finally", "in_with", "in_class"]


def test_fallback_import_in_except_is_recognised() -> None:
    text = (
        "try:\n    from nox_uv import session\n"
        "except ImportError:\n    from nox import session\n"
        "@session\ndef tests(s): ...\n"
    )
    assert _names(text) == ["tests"]


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
        "@nox.session\ndef t(session): ...\n"
    )
    assert [t.name for t in NoxProvider().discover(tmp_path)] == ["t"]
    assert not marker.exists()


def test_syntax_error_returns_empty(tmp_path: Path, caplog) -> None:
    (tmp_path / "noxfile.py").write_text("import nox\ndef (:\n")
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
