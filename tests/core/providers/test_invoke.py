from typing import TYPE_CHECKING

import pytest

from nur.core.discovery import discover
from nur.core.providers import invoke as invoke_module
from nur.core.providers.invoke import InvokeProvider, parse_tasks

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture


@pytest.mark.parametrize(
    ("imports", "decorator"),
    [
        ("from invoke import task", "task"),
        ("from invoke import task as t", "t()"),
        ("import invoke", "invoke.task"),
        ("import invoke.tasks as inv", "inv.task"),
        ("import invoke.tasks", "invoke.tasks.task"),
        ("import invoke.tasks", "invoke.task"),
        ("import invoke as inv", "inv.task(pre=[])"),
        ("from invoke.tasks import task", "task"),
        ("from invoke import tasks", "tasks.task"),
        ("from invoke import tasks as inv", "inv.task"),
    ],
)
def test_decorators(imports: str, decorator: str) -> None:
    tasks = parse_tasks(
        f"{imports}\n@{decorator}\ndef build_all(c):\n"
        '    """Build everything.\n\n    More details.\n    """\n'
    )
    assert len(tasks) == 1
    task = tasks[0]
    assert task.qualified_name == "invoke:build-all"
    assert task.argv_base == ("invoke", "build-all")
    assert task.run_argv(["--clean"]) == ["invoke", "build-all", "--clean"]
    assert task.description == "Build everything."
    assert task.definition == ""
    assert task.source_file == "tasks.py"
    assert not task.run_in_shell


def test_names_and_aliases() -> None:
    tasks = parse_tasks(
        "from invoke import task\n"
        '@task(name="release_all", aliases=("ship", "push_all"))\n'
        "def publish(c): ...\n"
    )
    assert [task.name for task in tasks] == ["release-all", "ship", "push-all"]
    assert all(task.argv_base == ("invoke", task.name) for task in tasks)
    assert all(task.description is None for task in tasks)


@pytest.mark.parametrize(
    "options",
    ["name=compute()", "name=123", "aliases=ALIASES", "**options", "klass=CustomTask"],
)
def test_dynamic_metadata_is_skipped(options: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({options})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "text",
    [
        "@task\ndef build(c): ...\n",
        "from other import task\n@task\ndef build(c): ...\n",
        "from .invoke import task\n@task\ndef build(c): ...\n",
        "from invoke import task\ntask = other\n@task\ndef build(c): ...\n",
        "from invoke import task\nif True:\n @task\n def build(c): ...\n",
        "from invoke import task\ndef helper():\n @task\n def build(c): ...\n",
        "from invoke import task\n@wrapper\n@task\ndef build(c): ...\n",
        "from invoke import task\n@task\ndef build(c): ...\nbuild = None\n",
        "from invoke import task\n@task\ndef build(c): ...\ndel build\n",
    ],
)
def test_unsupported_and_rebound_tasks(text: str) -> None:
    assert parse_tasks(text) == []


def test_detection_and_discovery_never_execute(tmp_path: Path) -> None:
    provider = InvokeProvider()
    assert not provider.detect(tmp_path)
    marker = tmp_path / "executed"
    (tmp_path / "tasks.py").write_text(
        f'from invoke import task\nopen({str(marker)!r}, "w").write("bad")\n'
        '@task\ndef build(c):\n raise RuntimeError("do not run")\n',
        encoding="utf-8",
    )
    assert provider.detect(tmp_path)
    assert discover(tmp_path).resolve("invoke:build").argv_base == ("invoke", "build")
    assert not marker.exists()


@pytest.mark.parametrize(
    "contents", [b"def broken(", b"\xff", b"from invoke import task\nreturn\n"]
)
def test_invalid_files_are_skipped(tmp_path: Path, caplog, contents: bytes) -> None:
    (tmp_path / "tasks.py").write_bytes(contents)
    assert InvokeProvider().discover(tmp_path) == []
    assert "skipping tasks.py" in caplog.text


def test_missing_file_is_skipped(tmp_path: Path, caplog) -> None:
    assert InvokeProvider().discover(tmp_path) == []
    assert "skipping tasks.py" in caplog.text


@pytest.mark.parametrize(
    ("name", "expected"), [("_build_all_", "_build-all_"), ("_", "_")]
)
def test_name_normalization(name: str, expected: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task(name={name!r})\ndef build(c): ...\n"
    )
    assert tasks[0].name == expected


@pytest.mark.parametrize(
    "replacement",
    [
        "def build(c): ...",
        "class build: ...",
        "from other import build",
        "import other as build",
        "from other import *",
        "build = None",
    ],
)
def test_conditional_task_rebinding(replacement: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            f"if True:\n {replacement}\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "replacement",
    ["def task(f): return f", "from other import task", "import other as task"],
)
def test_conditional_decorator_rebinding(replacement: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\nif True:\n {replacement}\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_source_encoding_cookie(tmp_path: Path) -> None:
    (tmp_path / "tasks.py").write_bytes(
        "# coding: latin-1\nfrom invoke import task\n@task\ndef build(c):\n"
        ' """Construire le café."""\n'.encode("latin-1")
    )
    assert InvokeProvider().discover(tmp_path)[0].description == "Construire le café."


@pytest.mark.parametrize(
    "expression",
    [
        "[build for build in range(3)]",
        "{build for build in range(3)}",
        "{build: build for build in range(3)}",
        "(build for build in range(3))",
    ],
)
def test_comprehension_targets_do_not_rebind_tasks(expression: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef build(c): ...\nvalues = {expression}\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_comprehension_assignment_expression_rebinds_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "values = [(build := None) for index in range(3)]\n"
        )
        == []
    )


def test_star_import_may_replace_existing_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\nfrom other import *\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "statement",
    [
        "if True:\n def helper():\n  build = None",
        "if True:\n class Helper:\n  build = None",
        "callback = lambda: (build := None)",
        "values = [lambda: (build := None) for index in range(3)]",
    ],
)
def test_nested_scopes_do_not_rebind_module_task(statement: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef build(c): ...\n{statement}\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_function_defaults_can_rebind_module_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "if True:\n def helper(value=(build := None)): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("options", ['unknown_aliases=("ship",)', "unknown=True"])
def test_unknown_decorator_options_are_skipped(options: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({options})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "options",
    [
        "default=True",
        "optional=['clean']",
        "help={'clean': 'Clean first'}",
        "autoprint=True",
        "positional=['clean']",
        "auto_shortflags=False",
        "iterable=['clean']",
        "incrementable=['clean']",
        "post=[]",
    ],
)
def test_supported_options_preserve_names(options: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task({options})\ndef build(c, clean=False): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("signature", ["", "*, context", "**kwargs"])
def test_missing_positional_context_is_skipped(signature: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task\ndef build({signature}): ...\n")
        == []
    )


@pytest.mark.parametrize("signature", ["context, /", "*args"])
def test_positional_context_signatures(signature: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef build({signature}): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("annotation", ["task: Callable", "task: Callable = None"])
def test_annotated_decorator_binding(annotation: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n{annotation}\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ([] if "=" in annotation else ["build"])


@pytest.mark.parametrize("annotation", ["build: Task", "build: Task = None"])
def test_annotated_task_binding(annotation: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef build(c): ...\n{annotation}\n"
    )
    assert [task.name for task in tasks] == ([] if "=" in annotation else ["build"])


@pytest.mark.parametrize(
    "header",
    [
        "def helper(value=(task := other)): ...",
        "async def helper(value=(task := other)): ...",
        "class Helper((task := other)): ...",
        "@identity((task := other))\ndef helper(): ...",
    ],
)
def test_definition_headers_rebind_decorators(header: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n{header}\n@task\ndef build(c): ...\n")
        == []
    )


def test_definition_default_rebinds_existing_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "def helper(value=(build := None)): ...\n"
        )
        == []
    )


def test_decorator_binding_is_captured_before_defaults() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c, value=(task := None)): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "annotation", ["(build := None).attribute: int", "values[(build := None)]: int"]
)
def test_annotation_target_expressions_can_rebind_tasks(annotation: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task\ndef build(c): ...\n{annotation}\n"
        )
        == []
    )


@pytest.mark.parametrize(
    ("imports", "mutation", "decorator"),
    [
        ("import invoke", "invoke.task = lambda f: f", "invoke.task"),
        ("import invoke", "del invoke.task", "invoke.task"),
        ("import invoke.tasks as inv", "inv.task = other", "inv.task"),
        ("import invoke.tasks", "invoke.tasks.task = other", "invoke.tasks.task"),
        ("import invoke as inv\nimport invoke", "inv.task = other", "invoke.task"),
        ("import invoke", 'setattr(invoke, "task", other)', "invoke.task"),
        ("import invoke", 'delattr(invoke, "task")', "invoke.task"),
    ],
)
def test_replaced_module_decorators_are_skipped(
    imports: str, mutation: str, decorator: str
) -> None:
    assert (
        parse_tasks(f"{imports}\n{mutation}\n@{decorator}\ndef build(c): ...\n") == []
    )


def test_module_mutation_preserves_imported_task_object() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\ninvoke.task = other\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_later_module_mutation_preserves_existing_tasks() -> None:
    tasks = parse_tasks(
        "import invoke\n@invoke.task\ndef build(c): ...\ninvoke.task = other\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_module_attribute_annotation_does_not_replace_decorator() -> None:
    tasks = parse_tasks(
        "import invoke\ninvoke.task: Callable\n@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    ("reimport", "decorator", "expected"),
    [
        ("import invoke", "invoke.task", []),
        ("import invoke as inv", "inv.task", []),
        ("import invoke.tasks as inv", "inv.task", ["build"]),
        ("from invoke import tasks as inv", "inv.task", ["build"]),
        ("from invoke import task as restored", "restored", []),
        ("from invoke.tasks import task as restored", "restored", ["build"]),
    ],
)
def test_mutated_exports_after_imports(
    reimport: str, decorator: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        "import invoke\ninvoke.task = lambda f: f\n"
        f"{reimport}\n@{decorator}\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == expected


@pytest.mark.parametrize(
    ("body", "decorator"),
    [
        ("invoke.task = lambda f: f", "invoke.task"),
        ("invoke.tasks.task = lambda f: f", "invoke.tasks.task"),
        ("import invoke as inv\n inv.task = lambda f: f", "invoke.task"),
        ("if True:\n  invoke.task = lambda f: f", "invoke.task"),
        ("class Nested:\n  invoke.task = lambda f: f", "invoke.task"),
        ('setattr(invoke, "task", other)', "invoke.task"),
    ],
)
def test_executed_class_mutations_invalidate_modules(body: str, decorator: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nclass Helper:\n {body}\n@{decorator}\ndef build(c): ...\n"
        )
        == []
    )


def test_class_local_shadow_does_not_mutate_imported_module() -> None:
    tasks = parse_tasks(
        "import invoke\nclass Helper:\n invoke = other\n invoke.task = replacement\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_method_body_is_not_executed_during_discovery() -> None:
    tasks = parse_tasks(
        "import invoke\nclass Helper:\n def mutate(self):\n  invoke.task = other\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_class_import_and_mutation_taints_later_module_import() -> None:
    assert (
        parse_tasks(
            "class Helper:\n import invoke\n invoke.task = other\n"
            "import invoke\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_nested_class_body_uses_module_bindings() -> None:
    assert (
        parse_tasks(
            "import invoke\nclass Helper:\n invoke = other\n"
            " class Nested:\n  invoke.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_nested_class_header_uses_enclosing_class_bindings() -> None:
    tasks = parse_tasks(
        "import invoke\nclass Helper:\n invoke = other\n"
        " class Nested((setattr(invoke, 'task', replacement), Base)[1]): ...\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("deletion", ["del invoke", "if True:\n  del invoke"])
def test_class_deletion_restores_module_lookup(deletion: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nclass Helper:\n invoke = other\n {deletion}\n"
            " invoke.task = replacement\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    ("options", "expected"),
    [("setup, pre=[setup]", []), ("setup", ["build"]), ("pre=[setup]", ["build"])],
)
def test_positional_dependencies_and_pre_option(
    options: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task({options})\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == expected


@pytest.mark.parametrize(
    "mutation",
    [
        "invoke.tasks = other",
        "del invoke.tasks",
        'setattr(invoke, "tasks", other)',
        'delattr(invoke, "tasks")',
    ],
)
def test_replaced_tasks_namespace(mutation: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\n{mutation}\n@invoke.tasks.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "reimport",
    [
        "import invoke",
        "import invoke.tasks",
        "import invoke.tasks as inv",
        "from invoke import tasks as inv",
    ],
)
def test_namespace_mutation_persists_after_imports(reimport: str) -> None:
    decorator = "inv.task" if "as inv" in reimport else "invoke.tasks.task"
    assert (
        parse_tasks(
            "import invoke\ninvoke.tasks = other\n"
            f"{reimport}\n@{decorator}\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    ("imports", "mutation", "reimport", "decorator"),
    [
        ("import invoke", "invoke.tasks.task = other", "import invoke", "invoke.task"),
        (
            "import invoke",
            "invoke.tasks.task = other",
            "from invoke import task",
            "task",
        ),
        ("import invoke", "invoke.tasks = other", "", "invoke.task"),
        (
            "import invoke\nfrom invoke import tasks as inv",
            "invoke.tasks = other",
            "",
            "inv.task",
        ),
        (
            "import invoke",
            "invoke.tasks = other",
            "from invoke.tasks import task",
            "task",
        ),
    ],
)
def test_mutations_preserve_independent_exports(
    imports: str, mutation: str, reimport: str, decorator: str
) -> None:
    tasks = parse_tasks(
        f"{imports}\n{mutation}\n{reimport}\n@{decorator}\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    ("reimport", "decorator"),
    [
        ("import invoke.tasks as inv", "inv.task"),
        ("from invoke.tasks import task", "task"),
    ],
)
def test_submodule_mutation_persists_after_imports(
    reimport: str, decorator: str
) -> None:
    assert (
        parse_tasks(
            "import invoke\ninvoke.tasks.task = other\n"
            f"{reimport}\n@{decorator}\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "body",
    [
        "if True:\n  import invoke as inv\n  inv.task = replacement",
        (
            "try:\n  import invoke as inv\n  inv.task = replacement\n"
            " except Exception:\n  pass"
        ),
        "for value in values:\n  import invoke as inv\n  inv.task = replacement",
        "with manager:\n  import invoke as inv\n  inv.task = replacement",
        "match value:\n  case 1:\n   import invoke as inv\n   inv.task = replacement",
    ],
)
def test_class_compound_import_precedes_mutation(body: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nclass Helper:\n {body}\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("write", ["invoke = replacement", "del invoke"])
def test_class_global_writes_invalidate_module_bindings(write: str) -> None:
    assert (
        parse_tasks(
            "import invoke\nclass Helper:\n global invoke\n"
            f" {write}\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_class_global_write_removes_existing_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "class Helper:\n global build\n build = None\n"
        )
        == []
    )


def test_nested_class_global_write_invalidates_module_binding() -> None:
    assert (
        parse_tasks(
            "import invoke\nclass Helper:\n class Nested:\n  global invoke\n"
            "  invoke = replacement\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_class_compound_global_write_invalidates_module_binding() -> None:
    assert (
        parse_tasks(
            "import invoke\nclass Helper:\n if True:\n  global invoke\n"
            "  invoke = replacement\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "branch",
    [
        "if True:\n  import invoke as inv",
        "if flag:\n  import invoke.tasks as inv\n else:\n  import invoke as inv",
    ],
)
def test_class_branch_alias_can_mutate_later(branch: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nclass Helper:\n {branch}\n inv.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_deep_class_blocks_are_inspected_once(mocker: MockerFixture) -> None:
    source = "import invoke\n"
    depth = 20
    for level in range(depth):
        source += " " * (level * 2) + f"class Helper{level}:\n"
        source += " " * (level * 2 + 1) + "if True:\n"
    source += " " * (depth * 2) + "invoke.task = replacement\n"
    source += "@invoke.task\ndef build(c): ...\n"
    inspect = mocker.spy(invoke_module, "_class_mutates_tasks")
    assert parse_tasks(source) == []
    assert inspect.call_count == depth


@pytest.mark.parametrize(
    "body",
    [
        "if True:\n import invoke as inv\n inv.task = replacement",
        "if True:\n import invoke as inv\ninv.task = replacement",
        "if True:\n import invoke as inv\n class Helper:\n  inv.task = replacement",
        (
            "try:\n import invoke as inv\n inv.task = replacement\n"
            "except Exception:\n pass"
        ),
    ],
)
def test_module_branches_track_alias_mutations(body: str) -> None:
    assert (
        parse_tasks(f"import invoke\n{body}\n@invoke.task\ndef build(c): ...\n") == []
    )


@pytest.mark.parametrize(
    "option",
    [
        "optional=1",
        "optional=None",
        "positional=1",
        "iterable=1",
        "incrementable=1",
        "help=1",
        "help='text'",
    ],
)
def test_invalid_literal_task_options_are_skipped(option: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({option})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "option",
    [
        "optional=[]",
        "positional=None",
        "iterable=None",
        "incrementable=0",
        "help=None",
        "help={}",
    ],
)
def test_valid_literal_task_options(option: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task({option})\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "mutation",
    [
        'invoke.__dict__["task"] = replacement',
        'vars(invoke)["task"] = replacement',
        'del invoke.__dict__["task"]',
        'del vars(invoke)["task"]',
    ],
)
def test_namespace_mapping_mutations(mutation: str) -> None:
    assert (
        parse_tasks(f"import invoke\n{mutation}\n@invoke.task\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "later", ["", "del second", "second = None", "@task\ndef second(c): ..."]
)
def test_colliding_defaults_respect_surviving_bindings(later: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef first(c): ...\n"
        f"@task(default=True)\ndef second(c): ...\n{later}\n"
    )
    expected = (
        [] if not later else ["first", "second"] if "@task" in later else ["first"]
    )
    assert [task.name for task in tasks] == expected


def test_default_aliases_do_not_collide() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True, aliases=['alias'])\n"
        "def build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build", "alias"]


@pytest.mark.parametrize(
    ("help_keys", "signature", "expected"),
    [
        ("{'missing': 'Help'}", "c, clean=False", []),
        ("{'c': 'Context'}", "c", []),
        ("{1: 'Help'}", "c, clean=False", []),
        ("{'clean': description}", "c, clean=False", ["build"]),
        ("{'dry_run': 'Help'}", "c, dry_run=False", ["build"]),
        ("{'dry-run': 'Help'}", "c, dry_run=False", ["build"]),
        ("{'dry_run': 'Help', 'dry-run': 'Help'}", "c, dry_run=False", []),
        ("{'dry-run': 'Help'}", "c, _dry_run_=False", ["build"]),
        ("{'clean': 'Help'}", "c, /, *, clean=False", ["build"]),
    ],
)
def test_literal_help_keys_match_parameters(
    help_keys: str, signature: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task(help={help_keys})\n"
        f"def build({signature}): ...\n"
    )
    assert [task.name for task in tasks] == expected


@pytest.mark.parametrize("name", ["deploy.prod", ".deploy", "deploy."])
def test_dotted_task_names_are_skipped(name: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(name={name!r})\ndef build(c): ...\n"
        )
        == []
    )


def test_dotted_aliases_are_skipped() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(aliases=['deploy.prod', 'deploy_all'])\n"
        "def build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build", "deploy-all"]
