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
        "@nox.parametrize('x', [1])\ndef b(s): ...\n"
    )
    assert _names(text) == []


def test_requires_nox_import() -> None:
    # Without a nox import, `nox.session` refers to nothing nox registers.
    assert _names("@nox.session\ndef a(session): ...\n") == []
    assert _names("@session\ndef a(session): ...\n") == []


def test_explicit_name_handling() -> None:
    text = (
        "import nox\nNAME = 'x'\n"
        "@nox.session(name=NAME)\ndef computed(session): ...\n"
        "@nox.session(name=None)\ndef default(session): ...\n"
        "@nox.session(name='')\ndef empty(session): ...\n"
    )
    assert _names(text) == ["default"]


def test_module_level_blocks_are_searched_but_function_bodies_are_not() -> None:
    text = (
        "import nox\nimport sys\n"
        "if sys.platform == 'linux':\n"
        "    @nox.session\n    def linux(session): ...\n"
        "else:\n"
        "    @nox.session\n    def other(session): ...\n"
        "try:\n"
        "    @nox.session\n    def in_try(session): ...\n"
        "except ImportError:\n    pass\n"
        "def factory():\n"
        "    @nox.session\n    def dynamic(session): ...\n"
        "class C:\n"
        "    @nox.session\n    def method(self, session): ...\n"
    )
    assert _names(text) == ["linux", "other", "in_try"]


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


def test_undecodable_file_returns_empty(tmp_path: Path, caplog) -> None:
    (tmp_path / "noxfile.py").write_bytes(b"\xff\xfe\x00bad")
    assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)
