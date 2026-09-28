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
    assert _names("import nox\nfor nox in [1]:\n    pass\n" + session) == []
    # An empty literal never assigns the loop target.
    assert _names("import nox\nfor nox in []:\n    pass\n" + session) == ["s"]
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


def test_else_raise_escapes_the_handlers() -> None:
    text = (
        "import nox\ntry:\n    pass\nexcept Exception:\n    pass\n"
        "else:\n    raise RuntimeError\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_only_catching_handlers_continue_after_a_raise() -> None:
    text = (
        "import nox\ntry:\n    raise TypeError\n"
        "except TypeError:\n    raise\nexcept ValueError:\n    pass\n"
        "@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_shadowed_exception_names_are_not_trusted() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    body = "try:\n    raise TypeError\nexcept Exception:\n    pass\n"
    assert _names("import nox\nException = ValueError\n" + body + session) == []
    assert _names("import nox\nTypeError = SystemExit\n" + body + session) == []
    base = "try:\n    raise TypeError\nexcept BaseException:\n    pass\n"
    assert _names("import nox\nBaseException = ValueError\n" + base + session) == []
    # A user-defined exception matched by its own name is still caught.
    own = (
        "import nox\nclass Boom(Exception): ...\n"
        "try:\n    raise Boom\nexcept Boom:\n    pass\n"
    )
    assert _names(own + session) == ["s"]


def test_handlers_are_tried_in_order() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    reraise_first = (
        "import nox\ntry:\n    raise TypeError\n"
        "except Exception:\n    raise\nexcept TypeError:\n    pass\n"
    )
    assert _names(reraise_first + session) == []
    earlier_passes = (
        "import nox\ntry:\n    raise TypeError\n"
        "except ValueError:\n    pass\nexcept TypeError:\n    pass\n"
    )
    assert _names(earlier_passes + session) == ["s"]


def test_class_body_raise_needs_a_catching_handler() -> None:
    text = (
        "import nox\ntry:\n    class C:\n        raise TypeError\n"
        "except ValueError:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_while_true_without_break_never_finishes() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nwhile True:\n    raise RuntimeError\n" + session) == []
    assert _names("import nox\nwhile 1:\n    pass\n" + session) == []
    breaks = "import nox\nwhile True:\n    break\n"
    assert _names(breaks + session) == ["s"]
    inner_only = (
        "import nox\nwhile True:\n    for _ in []:\n        break\n"
        "    raise RuntimeError\n"
    )
    assert _names(inner_only + session) == []


def test_constant_conditions_take_one_branch() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nif True:\n    raise RuntimeError\n" + session) == []
    assert _names("if 1:\n    import nox\n" + session) == ["s"]
    assert _names("import nox\nif False:\n    raise RuntimeError\n" + session) == ["s"]


def test_irrefutable_match_case_leaves_no_fallthrough() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    wildcard = "import nox\nmatch x:\n    case _:\n        raise RuntimeError\n"
    assert _names(wildcard + session) == []
    capture = "import nox\nmatch x:\n    case y:\n        raise RuntimeError\n"
    assert _names(capture + session) == []
    guarded = "import nox\nmatch x:\n    case _ if x:\n        raise RuntimeError\n"
    assert _names(guarded + session) == ["s"]


def test_finally_break_cancels_an_escaping_exception() -> None:
    text = (
        "import nox\nimport other\nwhile True:\n    try:\n        raise RuntimeError\n"
        "    finally:\n        nox = other\n        break\n"
        "@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []
    keeps = (
        "import nox\nwhile True:\n    try:\n        raise RuntimeError\n"
        "    finally:\n        break\n"
        "@nox.session\ndef s(session): ...\n"
    )
    assert _names(keeps) == ["s"]


def test_assert_false_ends_the_path() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nassert False\n" + session) == []
    assert _names("import nox\nassert 0, 'no'\n" + session) == []
    assert _names("import nox\nassert True\n" + session) == ["s"]


def test_unreachable_break_does_not_exit_the_loop() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    dead_if = "import nox\nwhile True:\n    if False:\n        break\n"
    assert _names(dead_if + session) == []
    after_raise = "import nox\nwhile True:\n    raise RuntimeError\n    break\n"
    assert _names(after_raise + session) == []
    live_if = "import nox\nwhile True:\n    if True:\n        break\n"
    assert _names(live_if + session) == ["s"]


def test_wildcard_as_pattern_is_irrefutable() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    named = "import nox\nmatch x:\n    case _ as y:\n        raise RuntimeError\n"
    assert _names(named + session) == []
    alternatives = "import nox\nmatch x:\n    case 1 | _:\n        raise RuntimeError\n"
    assert _names(alternatives + session) == []


def test_raise_expression_stores_reach_the_handler() -> None:
    text = (
        "import nox\ntry:\n    raise ValueError from (nox := RuntimeError())\n"
        "except ValueError:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_finally_raise_overrides_a_break() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    overridden = (
        "import nox\nwhile True:\n    try:\n        break\n"
        "    finally:\n        raise RuntimeError\n"
    )
    assert _names(overridden + session) == []
    in_finally = (
        "import nox\nwhile True:\n    try:\n        pass\n"
        "    finally:\n        break\n        raise RuntimeError\n"
    )
    assert _names(in_finally + session) == ["s"]


def test_constant_match_subject_selects_its_case() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    hit = "import nox\nmatch 1:\n    case 1:\n        raise RuntimeError\n"
    assert _names(hit + session) == []
    skip_other = (
        "import nox\nmatch 1:\n    case 2:\n        pass\n"
        "    case 1:\n        raise RuntimeError\n"
    )
    assert _names(skip_other + session) == []
    miss = "import nox\nmatch 1:\n    case 2:\n        raise RuntimeError\n"
    assert _names(miss + session) == ["s"]
    singleton = "import nox\nmatch None:\n    case None:\n        raise RuntimeError\n"
    assert _names(singleton + session) == []
    # Singletons compare by identity: 1 is not True.
    identity = "import nox\nmatch 1:\n    case True:\n        raise RuntimeError\n"
    assert _names(identity + session) == ["s"]


def test_invalid_handler_type_lets_the_exception_escape() -> None:
    text = (
        "import nox\ntry:\n    raise TypeError\n"
        "except 1:\n    pass\nexcept TypeError:\n    pass\n"
        "@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_raise_that_rebinds_its_own_class_is_not_matched_by_name() -> None:
    text = (
        "import nox\ntry:\n    raise TypeError from (TypeError := ValueError)\n"
        "except TypeError:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_finally_continue_overrides_a_break() -> None:
    text = (
        "import nox\nwhile True:\n    try:\n        break\n"
        "    finally:\n        continue\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_loop_else_runs_without_a_break() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    no_break = "import nox\nwhile False:\n    pass\nelse:\n    raise RuntimeError\n"
    assert _names(no_break + session) == []
    for_else = "import nox\nfor _ in []:\n    pass\nelse:\n    raise RuntimeError\n"
    assert _names(for_else + session) == []
    can_break = (
        "import nox\nfor x in y:\n    if x:\n        break\n"
        "else:\n    raise RuntimeError\n"
    )
    assert _names(can_break + session) == ["s"]


def test_instance_bound_name_is_not_a_catching_class() -> None:
    text = (
        "import nox\nE = TypeError()\ntry:\n    raise E\n"
        "except E:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_comprehension_and_lambda_locals_keep_module_bindings() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nitems = [nox for nox in ()]\n" + session) == ["s"]
    assert _names("import nox\nf = lambda nox: nox\n" + session) == ["s"]
    walrus = "import nox\nitems = [(nox := x) for x in ()]\n"
    assert _names(walrus + session) == []


def test_loop_that_cannot_enter_always_runs_else() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    for loop in ("while False:", "for x in []:", "for x in ():", "for x in '':"):
        text = f"import nox\n{loop}\n    break\nelse:\n    raise RuntimeError\n"
        assert _names(text + session) == [], loop
    enters = "import nox\nfor x in [1]:\n    break\nelse:\n    raise RuntimeError\n"
    assert _names(enters + session) == ["s"]


def test_constant_match_guards() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    true_guard = (
        "import nox\nmatch 1:\n    case 1 if True:\n        raise RuntimeError\n"
    )
    assert _names(true_guard + session) == []
    false_guard = (
        "import nox\nmatch 1:\n    case 1 if False:\n        raise RuntimeError\n"
    )
    assert _names(false_guard + session) == ["s"]


def test_nested_finally_continue_overrides_a_break() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    nested = (
        "import nox\nwhile True:\n    try:\n        break\n"
        "    finally:\n        if True:\n            continue\n"
    )
    assert _names(nested + session) == []
    both = (
        "import nox\nwhile True:\n    try:\n        break\n    finally:\n"
        "        if x:\n            continue\n"
        "        else:\n            raise RuntimeError\n"
    )
    assert _names(both + session) == []
    maybe = (
        "import nox\nwhile True:\n    try:\n        break\n"
        "    finally:\n        if x:\n            continue\n"
    )
    assert _names(maybe + session) == ["s"]


def test_annotation_only_keeps_the_binding() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nnox: object\n" + session) == ["s"]
    assert _names("import nox\nnox: object = 1\n" + session) == []


def test_caught_assert_false_continues() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    caught = "import nox\ntry:\n    assert False\nexcept AssertionError:\n    pass\n"
    assert _names(caught + session) == ["s"]
    uncaught = "import nox\ntry:\n    assert False\nexcept ValueError:\n    pass\n"
    assert _names(uncaught + session) == []


def test_constant_subject_matches_literal_alternatives() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    hit = "import nox\nmatch 1:\n    case 1 | 2:\n        raise RuntimeError\n"
    assert _names(hit + session) == []
    miss = "import nox\nmatch 1:\n    case 2 | 3:\n        raise RuntimeError\n"
    assert _names(miss + session) == ["s"]


def test_base_exceptions_escape_except_exception() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    for raised in ("BaseException", "BaseExceptionGroup('x', [])"):
        text = f"import nox\ntry:\n    raise {raised}\nexcept Exception:\n    pass\n"
        assert _names(text + session) == [], raised


def test_decorated_exception_class_is_not_trusted() -> None:
    text = (
        "import nox\n@factory\nclass E(Exception): ...\n"
        "try:\n    raise E\nexcept E:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == []


def test_same_name_match_needs_an_exception_class() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    handled = "try:\n    raise E\nexcept E:\n    pass\n"
    assert _names("import nox\nclass E: pass\n" + handled + session) == []
    int_class = "try:\n    raise int\nexcept int:\n    pass\n"
    assert _names("import nox\n" + int_class + session) == []
    chained = "import nox\nclass A(ValueError): ...\nclass E(A): ...\n"
    assert _names(chained + handled + session) == ["s"]


def test_for_over_a_non_empty_literal_runs_once() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    assert _names("import nox\nfor _ in [1]:\n    raise RuntimeError\n" + session) == []
    assert _names("import nox\nfor _ in 'x':\n    raise RuntimeError\n" + session) == []
    skips = "import nox\nfor _ in [1]:\n    continue\n    raise RuntimeError\n"
    assert _names(skips + session) == ["s"]
    unknown = "import nox\nfor _ in items:\n    raise RuntimeError\n"
    assert _names(unknown + session) == ["s"]


def test_inner_caught_raises_do_not_reach_outer_handlers() -> None:
    text = (
        "import nox\ntry:\n    try:\n        raise TypeError\n"
        "    except TypeError:\n        pass\n    raise ValueError\n"
        "except ValueError:\n    pass\n@nox.session\ndef s(session): ...\n"
    )
    assert _names(text) == ["s"]


def test_unary_and_container_conditions_are_constant() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    for test in ("not False", "not 0", "-1", "[1]", "(1,)", "{'a': 1}", "not []"):
        text = f"import nox\nif {test}:\n    raise RuntimeError\n"
        assert _names(text + session) == [], test
    for test in ("not True", "[]", "()", "{}", "-0"):
        text = f"import nox\nif {test}:\n    raise RuntimeError\n"
        assert _names(text + session) == ["s"], test
    starred = "import nox\nif [*items]:\n    raise RuntimeError\n"
    assert _names(starred + session) == ["s"]


def test_lambda_defaults_are_evaluated_in_the_enclosing_scope() -> None:
    text = "import nox\nf = lambda x=(nox := object()): x\n"
    assert _names(text + "@nox.session\ndef s(session): ...\n") == []


def test_literal_raise_causes_and_handler_types_are_invalid() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    cause = (
        "import nox\ntry:\n    raise ValueError from 1\nexcept ValueError:\n    pass\n"
    )
    assert _names(cause + session) == []
    none_cause = (
        "import nox\ntry:\n    raise ValueError from None\n"
        "except ValueError:\n    pass\n"
    )
    assert _names(none_cause + session) == ["s"]
    handler = (
        "import nox\ntry:\n    raise TypeError\n"
        "except []:\n    pass\nexcept TypeError:\n    pass\n"
    )
    assert _names(handler + session) == []


def test_exception_class_must_be_plain_top_level_and_defined_first() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    handled = "try:\n    raise E\nexcept E:\n    pass\n"
    meta = "import nox\nclass E(Exception, metaclass=type): ...\n"
    assert _names(meta + handled + session) == []
    nested = "import nox\nif x:\n    class E(Exception): ...\n"
    assert _names(nested + handled + session) == []
    later = "import nox\n" + handled + "class E(Exception): ...\n"
    assert _names(later + session) == []
    plain = "import nox\nclass E(Exception): ...\n"
    assert _names(plain + handled + session) == ["s"]


def test_match_captures_survive_a_failed_guard() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    false_guard = "import nox\nmatch 1:\n    case nox if False:\n        pass\n"
    assert _names(false_guard + session) == []
    maybe_guard = (
        "import nox\nmatch x:\n    case nox if y:\n        raise RuntimeError\n"
    )
    assert _names(maybe_guard + session) == []


def test_handlers_catch_known_subclasses() -> None:
    session = "@nox.session\ndef s(session): ...\n"

    def guarded(prelude: str, raised: str, caught: str) -> list[str]:
        body = f"try:\n    raise {raised}\nexcept {caught}:\n    pass\n"
        return _names("import nox\n" + prelude + body + session)

    assert guarded("", "FileNotFoundError", "OSError") == ["s"]
    assert guarded("", "KeyError", "LookupError") == ["s"]
    assert guarded("", "KeyError", "(TypeError, LookupError)") == ["s"]
    assert guarded("", "OSError", "FileNotFoundError") == []
    assert guarded("", "SystemExit", "Exception") == []
    user = "class A(ValueError): ...\nclass B(A): ...\n"
    assert guarded(user, "B", "ValueError") == ["s"]
    assert guarded(user, "B", "A") == ["s"]
    assert guarded(user, "A", "B") == []
    assert guarded("OSError = ValueError\n", "FileNotFoundError", "OSError") == []


def test_decorator_callable_is_resolved_before_its_arguments() -> None:
    text = "import nox\n@nox.session(tags=(nox := []))\ndef s(session): ...\n"
    assert _names(text) == ["s"]
    after = text + "@nox.session\ndef t(session): ...\n"
    assert _names(after) == ["s"]


def test_continue_makes_the_rest_of_the_body_unreachable() -> None:
    text = "import nox\nwhile True:\n    continue\n    break\n"
    assert _names(text + "@nox.session\ndef s(session): ...\n") == []


def test_nested_class_does_not_see_outer_class_names() -> None:
    text = (
        "class Outer:\n    import nox as local\n    class Inner:\n"
        "        @local.session\n        def bad(session): ...\n"
    )
    assert _names(text) == []
    module_level = (
        "import nox\nclass Outer:\n    class Inner:\n"
        "        @nox.session\n        def ok(session): ...\n"
    )
    assert _names(module_level) == ["ok"]


def test_constant_expression_handler_types_are_invalid() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    for handler in ("-1", "1 + 1", "0 or 1"):
        text = (
            "import nox\ntry:\n    raise TypeError\n"
            f"except {handler}:\n    pass\nexcept TypeError:\n    pass\n"
        )
        assert _names(text + session) == [], handler


def test_namespace_dict_mutation_is_distrusted() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    via_dict = "import nox\nnox.__dict__['session'] = print\n"
    assert _names(via_dict + session) == []
    via_vars = "import nox\nvars(nox)['session'] = print\n"
    assert _names(via_vars + session) == []


def test_except_star_is_never_trusted_to_catch() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    group = (
        "import nox\ntry:\n    raise ExceptionGroup('x', [TypeError()])\n"
        "except* ExceptionGroup:\n    pass\n"
    )
    assert _names(group + session) == []
    plain = "import nox\ntry:\n    raise TypeError\nexcept* TypeError:\n    pass\n"
    assert _names(plain + session) == []
    no_raise = "import nox\ntry:\n    pass\nexcept* TypeError:\n    pass\n"
    assert _names(no_raise + session) == ["s"]


def test_breaks_in_impossible_match_cases_are_unreachable() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    impossible = (
        "import nox\nwhile True:\n    match 1:\n        case 2:\n            break\n"
    )
    assert _names(impossible + session) == []
    possible = (
        "import nox\nwhile True:\n    match 1:\n        case 1:\n            break\n"
    )
    assert _names(possible + session) == ["s"]


def test_terminating_compound_statement_ends_the_break_scan() -> None:
    text = (
        "import nox\nwhile True:\n    if True:\n        raise RuntimeError\n    break\n"
    )
    assert _names(text + "@nox.session\ndef s(session): ...\n") == []


def test_signed_number_patterns_are_literals() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    miss = (
        "import nox\nwhile True:\n    match 1:\n        case -1:\n            break\n"
    )
    assert _names(miss + session) == []
    hit = "import nox\nmatch -1:\n    case -1:\n        raise RuntimeError\n"
    assert _names(hit + session) == []
    plain = "import nox\nmatch 1:\n    case -1:\n        raise RuntimeError\n"
    assert _names(plain + session) == ["s"]


def test_handler_that_cannot_catch_is_skipped() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    text = (
        "import nox\ntry:\n    raise ValueError\n"
        "except TypeError:\n    raise RuntimeError\nexcept ValueError:\n    pass\n"
    )
    assert _names(text + session) == ["s"]
    # An unknown base leaves the ancestry open, so the earlier handler may match.
    unknown_base = (
        "import nox\nclass E(ValueError, Mixin): ...\ntry:\n    raise E\n"
        "except TypeError:\n    raise RuntimeError\nexcept E:\n    pass\n"
    )
    assert _names(unknown_base + session) == []


def test_match_that_always_raises_ends_the_break_scan() -> None:
    text = (
        "import nox\nwhile True:\n    match 1:\n        case 1:\n"
        "            raise RuntimeError\n    break\n"
    )
    assert _names(text + "@nox.session\ndef s(session): ...\n") == []


def test_break_in_a_handler_that_cannot_run_is_unreachable() -> None:
    session = "@nox.session\ndef s(session): ...\n"
    dead = (
        "import nox\nwhile True:\n    try:\n        raise ValueError\n"
        "    except TypeError:\n        break\n"
    )
    assert _names(dead + session) == []
    live = (
        "import nox\nwhile True:\n    try:\n        raise ValueError\n"
        "    except ValueError:\n        break\n"
    )
    assert _names(live + session) == ["s"]
    implicit = (
        "import nox\nwhile True:\n    try:\n        f()\n        raise ValueError\n"
        "    except TypeError:\n        break\n"
    )
    assert _names(implicit + session) == ["s"]  # f() may raise TypeError.


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
