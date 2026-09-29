import warnings
from typing import TYPE_CHECKING

import pytest

from nur.core.providers.nox import NoxProvider, parse_noxfile

if TYPE_CHECKING:
    from pathlib import Path

NOXFILE = '''
"""Project automation."""
from __future__ import annotations

import os

import nox

nox.needs_version = ">=2024.3.2"
nox.options.sessions = ["tests"]
PYTHONS = ["3.13", "3.14"]


@nox.session
def tests(session):
    """Run the test suite.

    Longer explanation that is not the description.
    """
    session.run("pytest")


@nox.session(python=PYTHONS)
@nox.parametrize("django", ["4.2", "5.0"])
def lint(session, django):
    session.run("ruff", "check")


@nox.session(name="docs-build", tags=["docs"])
def docs(session):
    session.run("sphinx-build")


def helper(session):
    session.run("echo")
'''

SESSION = "@nox.session\ndef s(session): ...\n"


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
    (task,) = parse_noxfile("import nox\n" + SESSION)
    assert task.run_argv(["-k", "foo"]) == ["nox", "-s", "s", "--", "-k", "foo"]
    assert task.run_argv() == ["nox", "-s", "s"]


def test_import_aliases() -> None:
    text = (
        "import nox as n\n"
        "from nox import session\n"
        "from nox import session as s\n"
        "import nox.command\n"
        "@n.session\ndef a(session): ...\n"
        "@session\ndef b(session): ...\n"
        "@s(python='3.14')\ndef c(session): ...\n"
        "@nox.session\ndef d(session): ...\n"
    )
    assert _names(text) == ["a", "b", "c", "d"]
    assert _names("from nox import *\n@session\ndef a(session): ...\n") == ["a"]


def test_nox_uv_is_a_drop_in_replacement() -> None:
    text = "from nox_uv import session\n@session(name='a')\ndef b(s): ...\n"
    assert _names(text) == ["a"]
    assert _names("import nox_uv\n@nox_uv.session\ndef c(s): ...\n") == ["c"]


def test_ignores_unrelated_decorators() -> None:
    text = (
        "import nox\nimport other\nfrom other import session\n"
        "@other.session\ndef a(s): ...\n"
        "@session\ndef b(s): ...\n"
        "@nox.parametrize('x', [1])\ndef c(s): ...\n"
    )
    assert _names(text) == []
    assert _names(SESSION) == []  # `nox` is never imported.


def test_explicit_name_handling() -> None:
    # nox registers `name or func.__name__`, so None and "" fall back.
    text = (
        "import nox\nNAME = 'x'\nKW = {'name': 'y'}\n"
        "@nox.session(name=NAME)\ndef computed(session): ...\n"
        "@nox.session(**KW)\ndef splatted(session): ...\n"
        "@nox.session('3.14')\ndef positional(session): ...\n"
        "@nox.session(name=None)\ndef default(session): ...\n"
        "@nox.session(name='')\ndef empty(session): ...\n"
        "@nox.session()\ndef bare_call(session): ...\n"
    )
    assert _names(text) == ["default", "empty", "bare_call"]


def test_stacked_session_decorators_register_aliases() -> None:
    text = (
        "import nox\nNAME = 'x'\n"
        "@nox.session(name='test')\n@nox.session(name=NAME)\n"
        "@nox.session(name='tests')\ndef tests(session): ...\n"
    )
    assert _names(text) == ["tests", "test"]


def test_redefinition_keeps_first_position_and_last_definition() -> None:
    text = (
        "import nox\n"
        "@nox.session\ndef a(session):\n    '''old'''\n"
        "@nox.session\ndef b(session): ...\n"
        "@nox.session(name='a')\ndef c(session):\n    '''new'''\n"
    )
    tasks = parse_noxfile(text)
    assert [(t.name, t.description) for t in tasks] == [("a", "new"), ("b", None)]


def test_whitespace_only_docstring_has_no_description() -> None:
    text = 'import nox\n@nox.session\ndef t(s):\n    """\n   \n    """\n'
    (task,) = parse_noxfile(text)
    assert task.description is None


def test_bindings_follow_statement_order() -> None:
    assert _names(SESSION + "import nox\n") == []
    assert _names("import nox\nnox = object()\n" + SESSION) == []
    assert _names("import nox\ndef nox(): ...\n" + SESSION) == []
    assert _names("import nox\nclass nox: ...\n" + SESSION) == []
    assert _names("import nox\nimport other as nox\n" + SESSION) == []
    assert _names("import nox\nx = [(nox := 1)]\n" + SESSION) == []
    walrus_arg = "import nox\n@nox.session(tags=(nox := []))\ndef t(s): ...\n"
    assert _names(walrus_arg) == []
    second = SESSION.replace("s(", "t(")
    assert _names("import nox\n" + SESSION + "del nox\nimport nox\n" + second) == [
        "s",
        "t",
    ]


def test_class_and_async_bodies_are_not_searched() -> None:
    text = (
        "import nox\nclass C:\n    @nox.session\n    def method(session): ...\n"
        "async def f():\n    pass\n" + SESSION
    )
    assert _names(text) == ["s"]


def test_try_import_fallback() -> None:
    uv = (
        "try:\n    from nox_uv import session\n"
        "except ImportError:\n    from nox import session\n"
        "@session\ndef tests(s): ...\n"
    )
    assert _names(uv) == ["tests"]
    tomllib = (
        "import nox\ntry:\n    import tomllib\n"
        "except ModuleNotFoundError:\n    import tomli as tomllib\n" + SESSION
    )
    assert _names(tomllib) == ["s"]
    one_sided = "try:\n    import nox\nexcept ImportError:\n    pass\n" + SESSION
    assert _names(one_sided) == []
    disagree = "try:\n    import nox\nexcept ImportError:\n    import other as nox\n"
    assert _names(disagree + SESSION) == []


def test_guards_that_never_run_under_nox_are_skipped() -> None:
    rebind = "    import other as nox\n"  # Would invalidate `nox` if it ran.
    header = "from typing import TYPE_CHECKING\nimport nox\n"
    assert _names(header + "if TYPE_CHECKING:\n" + rebind + SESSION) == ["s"]
    shadowed = header + "TYPE_CHECKING = True\nif TYPE_CHECKING:\n" + rebind
    assert _names(shadowed + SESSION) == []
    main = "import nox\nif __name__ == '__main__':\n" + rebind
    assert _names(main + SESSION) == ["s"]
    reversed_main = "import nox\nif '__main__' == __name__:\n" + rebind
    assert _names(reversed_main + SESSION) == ["s"]
    renamed = "import nox\n__name__ = '__main__'\nif __name__ == '__main__':\n"
    assert _names(renamed + rebind + SESSION) == []


def test_if_blocks_merge_their_branches() -> None:
    config = "import nox\nflag = 1\nif flag:\n    nox.options.sessions = ['s']\n"
    assert _names(config + SESSION) == ["s"]
    both = "flag = 1\nif flag:\n    import nox\nelse:\n    import nox\n"
    assert _names(both + SESSION) == ["s"]
    one = "import nox\nflag = 1\nif flag:\n    import other as nox\n"
    assert _names(one + SESSION) == []
    guard = (
        "import sys\nimport nox\nif sys.version_info < (3, 9):\n    raise SystemExit\n"
    )
    assert _names(guard + SESSION) == ["s"]
    always = (
        "import nox\nflag = 1\nif flag:\n    raise SystemExit\n"
        "else:\n    raise SystemExit\n"
    )
    assert _names(always + SESSION) == []
    nested_def = (
        "import nox\nflag = 1\nif flag:\n"
        "    @nox.session\n    def hidden(session): ...\n"
    )
    assert _names(nested_def + SESSION) == ["s"]  # Only top-level defs register.


def test_try_whose_body_always_fails_is_unpredictable() -> None:
    text = "import nox\ntry:\n    missing\nexcept TypeError:\n    pass\n"
    assert _names(text + SESSION) == []


def test_try_blocks_merge_body_and_handlers() -> None:
    nested = (
        "import nox\ntry:\n    import tomllib as toml\nexcept ImportError:\n"
        "    try:\n        import toml\n    except ImportError:\n        toml = None\n"
    )
    assert _names(nested + SESSION) == ["s"]
    rebinding_body = (
        "import nox\ntry:\n    nox = 1\n    import nox\nexcept Exception:\n    pass\n"
    )
    assert _names(rebinding_body + SESSION) == []
    as_name = "import nox\ntry:\n    pass\nexcept ImportError as nox:\n    pass\n"
    assert _names(as_name + SESSION) == []
    with_finally = "import nox\ntry:\n    pass\nfinally:\n    x = 1\n"
    assert _names(with_finally + SESSION) == ["s"]


def test_raise_is_only_trusted_under_a_runtime_guard() -> None:
    guard = (
        "import nox\nimport sys\nif sys.version_info < (3, 9):\n    raise SystemExit\n"
    )
    assert _names(guard + SESSION) == ["s"]
    literal = "import nox\nif not False:\n    raise SystemExit\n"
    assert _names(literal + SESSION) == []
    in_try = "import nox\ntry:\n    raise TypeError\nexcept ValueError:\n    pass\n"
    assert _names(in_try + SESSION) == []
    in_class = "import nox\nclass C:\n    raise TypeError\n"
    assert _names(in_class + SESSION) == []
    in_function = "import nox\ndef helper():\n    raise TypeError\n"
    assert _names(in_function + SESSION) == ["s"]


def test_class_bodies_follow_module_rules() -> None:
    assert (
        _names("import nox\nclass C:\n    while True:\n        pass\n" + SESSION) == []
    )
    assert _names("import nox\nclass C:\n    raise TypeError\n" + SESSION) == []
    plain = "import nox\nclass C:\n    x = 1\n    def m(self): ...\n"
    assert _names(plain + SESSION) == ["s"]


def test_raising_guards_with_literal_parts_are_unpredictable() -> None:
    for test in ("flag or True", "not 0", "x and 1 == 1", "True"):
        text = f"import nox\nflag = x = 1\nif {test}:\n    raise RuntimeError\n"
        assert _names(text + SESSION) == [], test
    real = (
        "import nox\nimport sys\nif sys.version_info < (3, 9):\n    raise SystemExit\n"
    )
    assert _names(real + SESSION) == ["s"]


def test_directly_imported_parametrize_keeps_the_name() -> None:
    body = "@session\n@parametrize('a', [1])\ndef f(session, a): ...\n"
    assert _names("from nox import session, parametrize\n" + body) == ["f"]
    assert _names("from nox import *\n" + body) == ["f"]
    aliased = "from nox import session, parametrize as p\n"
    assert _names(aliased + body.replace("@parametrize", "@p")) == ["f"]
    other = "from nox import session\nfrom other import parametrize\n"
    assert _names(other + body) == []


def test_lower_decorators_may_rename_the_function() -> None:
    renamed = "import nox\nrename = print\n@nox.session\n@rename\ndef f(session): ...\n"
    assert _names(renamed) == []
    explicit = (
        "import nox\nrename = print\n"
        "@nox.session(name='x')\n@rename\ndef f(session): ...\n"
    )
    assert _names(explicit) == ["x"]
    parametrized = (
        "import nox\nrename = print\n@nox.session\n"
        "@nox.parametrize('a', [1])\ndef f(session, a): ...\n"
    )
    assert _names(parametrized) == ["f"]
    above = "import nox\nrename = print\n@rename\n@nox.session\ndef f(session): ...\n"
    assert _names(above) == ["f"]  # Applied after nox registered the name.


def test_type_checking_must_be_imported_first() -> None:
    rebind = "if TYPE_CHECKING:\n    import other as nox\n"
    never_imported = "import nox\n" + rebind
    assert _names(never_imported + SESSION) == []
    imported_late = "import nox\n" + rebind + "from typing import TYPE_CHECKING\n"
    assert _names(imported_late + SESSION) == []


def test_reading_a_never_bound_name_at_import_lists_nothing() -> None:
    assert _names("import nox\nif TYPE_CHECKING:\n    pass\n" + SESSION) == []
    assert _names("import nox\nprint(undefined)\n" + SESSION) == []
    in_body = "import nox\ndef helper():\n    return undefined\n"
    assert _names(in_body + SESSION) == ["s"]  # Only runs when called.
    in_default = "import nox\ndef helper(x=undefined): ...\n"
    assert _names(in_default + SESSION) == []


def test_reads_must_follow_their_bindings() -> None:
    assert _names("import nox\nprint(later)\nlater = 1\n" + SESSION) == []
    assert _names("import nox\nlater = 1\nprint(later)\n" + SESSION) == ["s"]
    in_function = "import nox\ndef setup():\n    helper = 1\nprint(helper)\n"
    assert _names(in_function + SESSION) == []
    comprehension = "import nox\nitems = [i for i in range(3)]\nprint(i)\n"
    assert _names(comprehension + SESSION) == []
    local_ok = "import nox\nitems = [i for i in range(3)]\n"
    assert _names(local_ok + SESSION) == ["s"]
    deleted = "import nox\nx = 1\ndel x\nprint(x)\n"
    assert _names(deleted + SESSION) == []
    one_branch = "import nox\nimport sys\nif sys.argv:\n    y = 1\nprint(y)\n"
    assert _names(one_branch + SESSION) == []
    guarded = "import nox\nimport sys\nif sys.argv:\n    print(missing)\n"
    assert _names(guarded + SESSION) == ["s"]  # Only that branch fails.


def test_annotations_are_reads_unless_postponed() -> None:
    header = (
        "from typing import TYPE_CHECKING\nimport nox\n"
        "if TYPE_CHECKING:\n    from nox import Session\n"
    )
    typed = "@nox.session\ndef s(session: Session) -> None: ...\n"
    assert _names(header + typed) == []  # Eager annotations raise NameError.
    future = "from __future__ import annotations\n"
    assert _names(future + header + typed) == ["s"]
    module_level = "x: Session = 1\n"
    assert _names(future + header + module_level + typed) == ["s"]
    default = "@nox.session\ndef s(session, x=Session): ...\n"
    assert _names(future + header + default) == []  # Defaults still run.


def test_comprehension_first_iterable_reads_the_enclosing_scope() -> None:
    assert _names("import nox\nitems = [x for x in x]\n" + SESSION) == []
    ok = "import nox\nxs = [1]\nitems = [x for x in xs if x]\n"
    assert _names(ok + SESSION) == ["s"]
    nested = "import nox\nxs = [[1]]\nitems = [y for x in xs for y in x]\n"
    assert _names(nested + SESSION) == ["s"]
    later_target = "import nox\nitems = [x for x in [1] for y in y]\n"
    assert _names(later_target + SESSION) == []


def test_session_keywords_must_be_accepted() -> None:
    assert _names("import nox\n@nox.session(typo=True)\ndef s(session): ...\n") == []
    uv_on_nox = "import nox\n@nox.session(uv_groups=['dev'])\ndef s(session): ...\n"
    assert _names(uv_on_nox) == []
    uv = (
        "from nox_uv import session\n@session(uv_groups=['dev'])\ndef s(session): ...\n"
    )
    assert _names(uv) == ["s"]
    fallback = (
        "try:\n    from nox_uv import session\n"
        "except ImportError:\n    from nox import session\n"
    )
    assert _names(fallback + "@session(tags=['x'])\ndef s(session): ...\n") == ["s"]
    assert _names(fallback + "@session(uv_groups=['x'])\ndef s(session): ...\n") == []


def test_star_imports_bind_only_known_names() -> None:
    body = "from nox import *\n@session\ndef s(session): ...\n"
    assert _names(
        "from nox import *\noptions.sessions = []\n"
        + SESSION.replace("@nox.session", "@session")
    ) == ["s"]
    assert _names("from nox import *\nprint(definitely_missing)\n" + body) == []
    assert _names("from nox_uv import *\n@session\ndef s(session): ...\n") == []


def test_display_conditions_have_fixed_truth() -> None:
    for test in ("(flag,)", "[flag]", "{flag: 1}", "f'{flag}'", "not (flag,)"):
        text = f"import nox\nflag = 1\nif {test}:\n    raise RuntimeError\n"
        assert _names(text + SESSION) == [], test
    membership = (
        "import nox\nimport sys\nif sys.platform in ('win32', 'cygwin'):\n"
        "    raise SystemExit\n"
    )
    assert _names(membership + SESSION) == ["s"]


@pytest.mark.parametrize(
    "statement",
    [
        "for _ in []:\n    pass\n",
        "while False:\n    pass\n",
        "with open('f'):\n    pass\n",
        "match x:\n    case _:\n        pass\n",
        "raise RuntimeError\n",
        "try:\n    raise RuntimeError\nexcept ValueError:\n    raise\n",
        "assert x\n",
        "try:\n    import os\nexcept* ImportError:\n    pass\n",
        "from helpers import *\n",
    ],
)
def test_module_level_control_flow_lists_nothing(statement: str) -> None:
    assert _names("import nox\nx = 1\n" + statement + SESSION) == []


def test_global_declarations_make_a_name_untrackable() -> None:
    in_function = "import nox\ndef setup():\n    global nox\n    nox = 1\nsetup()\n"
    assert _names(in_function + SESSION) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "nox.session = print\n",
        "del nox.session\n",
        "setattr(nox, 'session', print)\n",
        "nox.__dict__['session'] = print\n",
        "vars(nox)['session'] = print\n",
        "globals()['nox'] = object()\n",
        "locals()['nox'] = object()\n",
        "exec('nox = object()')\n",
        "eval('(nox := 1)')\n",
        "import builtins\nbuiltins.setattr(nox, 'session', print)\n",
        "from builtins import setattr as s\ns(nox, 'session', print)\n",
        "nox.__setattr__('session', print)\n",
        "nox.__delattr__('session')\n",
        "class C:\n    nox.session = print\n",
    ],
)
def test_namespace_mutation_lists_nothing(mutation: str) -> None:
    assert _names("import nox\n" + mutation + SESSION) == []


def test_ordinary_nox_configuration_is_allowed() -> None:
    config = "import nox\nnox.needs_version = '>=2024'\nnox.options.sessions = []\n"
    assert _names(config + SESSION) == ["s"]


def test_discovery_never_executes_the_noxfile(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    (tmp_path / "noxfile.py").write_text(
        f"import nox, pathlib\npathlib.Path({str(marker)!r}).touch()\n" + SESSION,
        encoding="utf-8",
    )
    assert [t.name for t in NoxProvider().discover(tmp_path)] == ["s"]
    assert not marker.exists()


def test_syntax_warnings_are_not_emitted() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _names('import nox\nPAT = "\\d+"\n' + SESSION) == ["s"]


def test_syntax_error_returns_empty(tmp_path: Path, caplog) -> None:
    (tmp_path / "noxfile.py").write_text("import nox\ndef (:\n", encoding="utf-8")
    assert NoxProvider().discover(tmp_path) == []
    assert any("noxfile.py" in r.message for r in caplog.records)


def test_file_that_cannot_compile_lists_nothing(tmp_path: Path, caplog) -> None:
    for broken in ("@nox.session(name='a', name='b')\ndef t(s): ...\n", "return\n"):
        (tmp_path / "noxfile.py").write_text(
            "import nox\n" + SESSION + broken, encoding="utf-8"
        )
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
