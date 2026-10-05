import time
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


@pytest.mark.parametrize("module", ["invoke", "invoke.tasks"])
def test_known_invoke_star_import(module: str) -> None:
    tasks = parse_tasks(f"from {module} import *\n@task\ndef build(c): ...\n")
    assert [task.name for task in tasks] == ["build"]


def test_star_import_respects_mutated_export() -> None:
    assert (
        parse_tasks(
            "import invoke\ninvoke.task = replacement\nfrom invoke import *\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("option", ["pre=1", "post=1", "pre=1.5", "post=True"])
def test_invalid_literal_hooks_are_skipped(option: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({option})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    ("imports", "mutation"),
    [
        ("import builtins", "builtins.setattr(invoke, 'task', replacement)"),
        ("import builtins as b", "b.delattr(invoke, 'task')"),
        (
            "from builtins import setattr as replace",
            "replace(invoke, 'task', replacement)",
        ),
        ("from builtins import delattr as remove", "remove(invoke, 'task')"),
        ("import builtins", "builtins.vars(invoke)['task'] = replacement"),
        (
            "from builtins import vars as namespace",
            "namespace(invoke)['task'] = replacement",
        ),
    ],
)
def test_qualified_and_aliased_builtin_mutations(imports: str, mutation: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\n{imports}\n{mutation}\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("module", ["invoke", "invoke.tasks"])
def test_star_import_preserves_existing_tasks(module: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef build(c): ...\nfrom {module} import *\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("module", ["invoke", "invoke.tasks"])
def test_star_import_replaces_exported_task_binding(module: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task\ndef Task(c): ...\nfrom {module} import *\n"
    )
    assert tasks == []


@pytest.mark.parametrize(
    "option", ["pre=['setup']", "post=[1]", "pre='setup'", "post={'key': 'value'}"]
)
def test_literal_hook_collections_are_skipped(option: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task({option})\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "shadow",
    [
        "setattr = lambda *args: None",
        "def setattr(*args): pass",
        "from other import setattr",
    ],
)
def test_shadowed_builtin_is_not_a_mutation(shadow: str) -> None:
    tasks = parse_tasks(
        f"import invoke\n{shadow}\nsetattr(invoke, 'task', None)\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_deleted_shadow_restores_builtin() -> None:
    assert (
        parse_tasks(
            "import invoke\nsetattr = other\ndel setattr\n"
            "setattr(invoke, 'task', None)\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("dependency", ["'setup'", "1", "None", "[]", "{}", "False"])
def test_literal_positional_dependencies_are_skipped(dependency: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task({dependency})\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "copy", ["saved = build", "saved: Task = build", "saved = second = build"]
)
def test_copied_defaults_collide(copy: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(default=True)\ndef build(c): ...\n{copy}\n"
        )
        == []
    )


@pytest.mark.parametrize("cleanup", ["saved = None", "del saved"])
def test_removed_default_copy_does_not_collide(cleanup: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
        "saved = build\n"
        f"{cleanup}\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_copying_default_to_same_binding_does_not_collide() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
        "build = build\n@task\ndef publish(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build", "publish"]


@pytest.mark.parametrize("name", ["--help", "-h", "-build"])
def test_flag_like_task_names_are_skipped(name: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(name={name!r})\ndef build(c): ...\n"
        )
        == []
    )


def test_flag_like_aliases_are_skipped() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(aliases=['-h', '--help', 'compile'])\n"
        "def build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build", "compile"]


@pytest.mark.parametrize(
    "alias",
    [
        "alias = invoke",
        "alias: Module = invoke",
        "alias = second = invoke",
        "alias = invoke.tasks",
    ],
)
def test_copied_module_alias_mutations(alias: str) -> None:
    decorator = "invoke.tasks.task" if "invoke.tasks" in alias else "invoke.task"
    assert (
        parse_tasks(
            f"import invoke\n{alias}\nalias.task = replacement\n"
            f"@{decorator}\ndef build(c): ...\n"
        )
        == []
    )


def test_copied_module_alias_in_class_mutates_module() -> None:
    assert (
        parse_tasks(
            "import invoke\nclass Helper:\n alias = invoke\n alias.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_replaced_module_alias_does_not_mutate_original() -> None:
    tasks = parse_tasks(
        "import invoke\nalias = invoke\nalias = other\nalias.task = replacement\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "copy", ["saved = build", "saved: Task = build", "saved = other = build"]
)
@pytest.mark.parametrize("replacement", ["build = None", "del build"])
def test_task_survives_under_copied_binding(copy: str, replacement: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(name='compile', aliases=['make'])\n"
        f'def build(c):\n """Build the project."""\n{copy}\n{replacement}\n'
    )
    assert [task.name for task in tasks] == ["compile", "make"]
    assert all(task.description == "Build the project." for task in tasks)


def test_task_copy_chain_survives_deleted_intermediate_bindings() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c): ...\n"
        "saved = build\nsecond = saved\ndel build, saved\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_only_surviving_default_copy_remains_discoverable() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
        "saved = build\ndel build\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "mutation",
    [
        "invoke.__dict__.update(task=replacement)",
        "invoke.__dict__.update({'task': replacement})",
        "vars(invoke).update(task=replacement)",
        "invoke.__dict__.__setitem__('task', replacement)",
        "invoke.__dict__.__delitem__('task')",
        "invoke.__dict__.pop('task')",
        "invoke.__dict__.clear()",
        "invoke.__dict__.popitem()",
        "invoke.__dict__.update(changes)",
    ],
)
def test_namespace_mapping_methods_invalidate_decorator(mutation: str) -> None:
    assert (
        parse_tasks(f"import invoke\n{mutation}\n@invoke.task\ndef build(c): ...\n")
        == []
    )


@pytest.mark.parametrize(
    "expression", ["invoke.__dict__.get('task')", "invoke.__dict__.update(other=value)"]
)
def test_namespace_mapping_methods_preserve_unmodified_exports(expression: str) -> None:
    tasks = parse_tasks(
        f"import invoke\n{expression}\n@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_root_mapping_clear_preserves_independent_submodule_export() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import tasks\ninvoke.__dict__.clear()\n"
        "@tasks.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "copy",
    [
        "(alias,) = (invoke,)",
        "[alias] = [invoke]",
        "(other, (alias,)) = (0, (invoke,))",
    ],
)
def test_destructured_module_alias_mutates_original(copy: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\n{copy}\nalias.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("dependency", ["lambda c: None", "helper", "saved", "closure"])
def test_plain_callable_is_not_a_positional_task_dependency(dependency: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\ndef helper(c): ...\n"
            "saved = helper\nclosure = lambda c: None\n"
            f"@task({dependency})\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "definition", ["async def helper(c): ...", "class helper: pass"]
)
def test_other_plain_callables_are_not_task_dependencies(definition: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n{definition}\n@task(helper)\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("copy", ["(saved,) = (build,)", "[saved] = [build]"])
def test_destructured_default_copy_collides(copy: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(default=True)\ndef build(c): ...\n{copy}\n"
        )
        == []
    )


def test_destructured_task_survives_original_deletion() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c): ...\n"
        "(saved,) = (build,)\ndel build\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("copy", ["(alias := invoke)", "if (alias := invoke): pass"])
def test_named_expression_module_alias_mutates_original(copy: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\n{copy}\nalias.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("block", ["if True:", "for item in [1]:", "with manager:"])
def test_compound_task_copy_survives_deleted_original(block: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c): ...\n"
        f"{block}\n saved = build\n second = saved\ndel build, saved\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_compound_default_task_copy_collides() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
            "if True:\n saved = build\n"
        )
        == []
    )


@pytest.mark.parametrize("replacement", ["saved = None", "del saved"])
def test_replaced_compound_default_copy_does_not_collide(replacement: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
        f"if True:\n saved = build\n {replacement}\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("final", ["del build", "build = None"])
def test_finally_removes_task_after_try_outcomes(final: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            f"try:\n pass\nexcept Exception:\n pass\nfinally:\n {final}\n"
        )
        == []
    )


def test_finally_removes_copied_default_without_collision() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=True)\ndef build(c): ...\n"
        "try:\n saved = build\nfinally:\n del saved\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("hook", ["[setup, 'bad']", "(setup, None)", "{setup, 7}"])
@pytest.mark.parametrize("option", ["pre", "post"])
def test_mixed_hook_collections_reject_literal_members(hook: str, option: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task({option}={hook})\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "mutation", ["setattr(invoke, attr, replacement)", "delattr(invoke, attr)"]
)
def test_computed_builtin_attribute_mutation_invalidates_exports(mutation: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nattr = 'task'\n{mutation}\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("condition", ["True", "flag"])
def test_replaced_task_is_not_restored_by_alternative_branch(condition: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            f"if {condition}:\n build = None\nelse:\n pass\n"
        )
        == []
    )


@pytest.mark.parametrize("option", ["pre", "post"])
def test_plain_function_hook_is_rejected(option: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\ndef helper(c): ...\n"
            f"@task({option}=[helper])\ndef build(c): ...\n"
        )
        == []
    )


def test_help_literal_keys_checked_alongside_unpacking() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(help={'missing': 'text', **extra})\n"
            "def build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("mapping", ["globals()", "locals()"])
@pytest.mark.parametrize(
    "mutation",
    ["{mapping}['task'] = replacement", "{mapping}.update(task=replacement)"],
)
def test_global_namespace_mapping_replaces_decorator(
    mapping: str, mutation: str
) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n{mutation.format(mapping=mapping)}\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_class_locals_mapping_does_not_replace_module_decorator() -> None:
    tasks = parse_tasks(
        "from invoke import task\nclass Helper:\n locals()['task'] = replacement\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "loop", ["for item in []:", "for item in items:", "while False:", "while flag:"]
)
def test_loop_copy_must_survive_zero_iteration_outcome(loop: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            f"{loop}\n saved = build\ndel build\n"
        )
        == []
    )


def test_extra_positional_only_task_parameter_is_rejected() -> None:
    assert (
        parse_tasks("from invoke import task\n@task\ndef build(c, target, /): ...\n")
        == []
    )


def test_positional_only_context_is_supported() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c, /, target): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "copy", ["decorator = task", "(decorator,) = (task,)", "decorator: Callable = task"]
)
def test_assigned_task_decorator_alias_is_supported(copy: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n{copy}\n@decorator\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("options", ["unknown=True", "optional=None"])
def test_fatal_decorator_suppresses_all_module_tasks(options: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef before(c): ...\n"
            f"@task({options})\ndef broken(c): ...\n@task\ndef after(c): ...\n"
        )
        == []
    )


def test_unsupported_computed_metadata_preserves_other_tasks() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(name=computed)\ndef dynamic(c): ...\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_variadic_parameters_after_context_are_rejected() -> None:
    assert (
        parse_tasks("from invoke import task\n@task\ndef build(c, *items): ...\n") == []
    )


def test_variadic_context_remains_supported() -> None:
    tasks = parse_tasks("from invoke import task\n@task\ndef build(*items): ...\n")
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("decorator", ["@task", "@task()", "@task(positional=None)"])
def test_contextless_implicit_task_is_module_fatal(decorator: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n{decorator}\ndef broken(): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_contextless_explicit_positionals_do_not_fail_construction() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(positional=[])\ndef broken(): ...\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_innermost_contextless_task_is_module_fatal() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@wrapper\n@task\ndef broken(): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_fatal_async_decorator_suppresses_module_tasks() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(unknown=True)\nasync def broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_repeated_loop_can_remove_copied_task() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "for item in [1, 2]:\n saved = build\n build = None\n"
        )
        == []
    )


def test_unmatched_pattern_does_not_create_task_copy() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "match value:\n case 1:\n  saved = build\n"
            " case 2:\n  saved = build\ndel build\n"
        )
        == []
    )


def test_keyword_only_tasks_are_not_constructor_fatal() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef unsupported(*, option=False): ...\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_irrefutable_match_preserves_copied_task() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c): ...\n"
        "match value:\n case _:\n  saved = build\ndel build\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_nested_repeated_loops_remain_fast() -> None:
    source = "from invoke import task\n@task\ndef build(c): ...\n"
    for depth in range(20):
        source += " " * depth + "for item in [1, 2]:\n"
    source += " " * 20 + "saved = build\n"
    started = time.perf_counter()
    assert [task.name for task in parse_tasks(source)] == ["build"]
    assert time.perf_counter() - started < 2


def test_continue_does_not_create_unreachable_task_copy() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "for item in [1]:\n saved = None\n continue\n saved = build\ndel build\n"
        )
        == []
    )


@pytest.mark.parametrize("copy", ["saved, *rest = (build,)", "*rest, saved = (build,)"])
def test_starred_default_task_copy_collides(copy: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(default=True)\ndef build(c): ...\n{copy}\n"
        )
        == []
    )


def test_starred_unpacking_module_alias_mutates_original() -> None:
    assert (
        parse_tasks(
            "import invoke\nalias, *rest = (invoke,)\nalias.task = replacement\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_starred_task_copy_survives_deleted_original() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef build(c): ...\n"
        "*rest, saved = (1, build)\ndel build\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "expression",
    [
        "False and (saved := build)",
        "True or (saved := build)",
        "(saved := build) if False else None",
    ],
)
def test_unreachable_named_expression_does_not_copy_task(expression: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            f"{expression}\ndel build\n"
        )
        == []
    )


@pytest.mark.parametrize("option", ["pre", "post"])
def test_task_object_direct_hook_is_rejected(option: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef setup(c): ...\n"
        f"@task({option}=setup)\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["setup"]


@pytest.mark.parametrize("condition", ["True", "False"])
def test_fatal_decorator_in_literal_branch(condition: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\nif {condition}:\n @task(unknown=True)\n"
        " def broken(c): ...\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ([] if condition == "True" else ["build"])


@pytest.mark.parametrize(
    "options", ["setup, pre=[setup]", "setup, pre=[setup], klass=Custom"]
)
def test_positional_pre_conflict_is_module_fatal(options: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef setup(c): ...\n"
            f"@task({options})\ndef broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "block",
    [
        "for build in [1]:\n pass",
        "with manager as build:\n pass",
        "try:\n pass\nexcept Exception as build:\n pass",
        "match value:\n case build:\n  pass",
    ],
)
def test_compound_header_can_replace_task_binding(block: str) -> None:
    assert (
        parse_tasks(f"from invoke import task\n@task\ndef build(c): ...\n{block}\n")
        == []
    )


def test_literal_false_branch_preserves_imported_decorator() -> None:
    tasks = parse_tasks(
        "from invoke import task\nif False:\n task = None\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_literal_false_branch_cannot_hide_fatal_decorator() -> None:
    assert (
        parse_tasks(
            "import invoke\nfrom invoke import task\nif False:\n task = None\n"
            "@task(unknown=True)\ndef broken(c): ...\n@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_fatal_positional_pre_conflict_inside_literal_branch() -> None:
    assert (
        parse_tasks(
            "from invoke import task\nif True:\n @task\n def setup(c): ...\n"
            " saved = setup\n @task(saved, pre=[saved])\n def broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("help_value", ["'bad'", "7", "(1,)"])
def test_fatal_literal_help_suppresses_all_tasks(help_value: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(help={help_value})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("jump", ["break", "continue"])
@pytest.mark.parametrize("position", ["before", "after"])
def test_fatal_loop_prefix_respects_jumps(jump: str, position: str) -> None:
    broken = " @task(unknown=True)\n def broken(c): ...\n"
    body = broken + f" {jump}\n" if position == "before" else f" {jump}\n" + broken
    tasks = parse_tasks(
        f"from invoke import task\nfor item in [1]:\n{body}@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ([] if position == "before" else ["build"])


@pytest.mark.parametrize(
    "argument", ["lambda c: None", "helper", "task", "invoke.task"]
)
def test_known_callable_positional_dispatch_is_module_fatal(argument: str) -> None:
    assert (
        parse_tasks(
            "import invoke\nfrom invoke import task\ndef helper(c): ...\n"
            f"@task({argument})\ndef broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "options", ["unknown=True", "help='bad'", "optional=None", "lambda c: None"]
)
def test_unpacked_custom_constructor_options_are_not_definitely_fatal(
    options: str,
) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task({options}, **options)\ndef custom(c): ...\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_fatal_class_decorator_suppresses_module_tasks() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(unknown=True)\nclass Broken: pass\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_fatal_decorator_in_finally_suppresses_module_tasks() -> None:
    assert (
        parse_tasks(
            "from invoke import task\ntry:\n pass\nfinally:\n @task(unknown=True)\n"
            " def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_nonempty_dictionary_loop_has_fatal_prefix() -> None:
    assert (
        parse_tasks(
            "from invoke import task\nfor item in {1: 2}:\n @task(unknown=True)\n"
            " def broken(c): ...\n break\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("option", ["pre", "post"])
def test_async_task_objects_are_not_direct_hooks(option: str) -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\nasync def setup(c): ...\n"
            f"@task({option}=setup)\ndef build(c): ...\n"
        )
        == []
    )


def test_async_default_task_participates_in_collisions() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(default=True)\nasync def setup(c): ...\n"
            "@task(default=True)\ndef build(c): ...\n"
        )
        == []
    )


def test_computed_name_task_objects_are_not_direct_hooks() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(name=computed)\ndef setup(c): ...\n"
            "@task(pre=setup)\ndef build(c): ...\n"
        )
        == []
    )


def test_replaced_decorator_in_try_is_not_assumed_fatal_in_finally() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\ntry:\n task = replacement\n"
        "finally:\n @task(unknown=True)\n def other(c): ...\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_unrolled_loop_reassigns_target_before_each_iteration() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task\ndef build(c): ...\n"
            "for saved in [1, 2]:\n copy = saved\n saved = build\n"
            "del build, saved\n"
        )
        == []
    )


@pytest.mark.parametrize("hook", ["task", "invoke.task", "invoke.tasks.task"])
@pytest.mark.parametrize("option", ["pre", "post"])
def test_decorator_functions_are_not_hook_members(hook: str, option: str) -> None:
    assert (
        parse_tasks(
            "import invoke.tasks\nfrom invoke import task\n"
            f"@task({option}=[{hook}])\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("aliases", ["7", "None", "False", "1.5"])
def test_noniterable_aliases_suppress_all_module_tasks(aliases: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(aliases={aliases})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_unreachable_handler_cannot_hide_fatal_finalizer() -> None:
    assert (
        parse_tasks(
            "import invoke\nfrom invoke import task\ntry:\n pass\n"
            "except Exception:\n task = replacement\nfinally:\n"
            " @task(unknown=True)\n def broken(c): ...\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


def test_loop_target_replaces_decorator_before_fatal_analysis() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\n"
        "def replacement(**kwargs): return lambda f: f\n"
        "for task in [replacement]:\n @task(unknown=True)\n def other(c): ...\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("pattern", ["_", "captured"])
def test_irrefutable_match_case_has_fatal_decorator(pattern: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\nmatch 1:\n case {pattern}:\n"
            "  @task(unknown=True)\n  def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_match_capture_replaces_decorator_before_fatal_analysis() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\nmatch replacement:\n case task:\n"
        "  @task(unknown=True)\n  def other(c): ...\n@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_uncaught_fatal_decorator_inside_try_finally() -> None:
    assert (
        parse_tasks(
            "from invoke import task\ntry:\n @task(unknown=True)\n"
            " def broken(c): ...\nfinally:\n pass\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "loop", ["for item in []", "for item in ()", "while False", "for item in [1, 2]"]
)
def test_guaranteed_loop_else_has_fatal_decorator(loop: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n{loop}:\n pass\nelse:\n"
            " @task(unknown=True)\n def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_empty_loop_with_unreachable_break_has_fatal_else() -> None:
    assert (
        parse_tasks(
            "from invoke import task\nfor item in []:\n break\nelse:\n"
            " @task(unknown=True)\n def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_finally_break_can_suppress_fatal_decorator_error() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\nfor item in [1]:\n try:\n"
        "  @task(unknown=True)\n  def broken(c): ...\n finally:\n  break\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "options", ["iterable=1", "incrementable=1", "positional=1", "help=[1]", "help={1}"]
)
def test_parser_fatal_options_suppress_sibling_tasks(options: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task({options})\n"
            "def broken(c, item=False): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("options", ["iterable=1", "incrementable=1"])
def test_argumentless_task_does_not_make_membership_options_fatal(options: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task({options})\ndef other(c): ...\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize("options", ["positional=1", "help=[1]", "help={1}"])
def test_argumentless_task_can_have_parser_fatal_options(options: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task({options})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("constructor", ["1", "None", "False", "'bad'", "[]", "{}"])
def test_noncallable_literal_constructor_is_module_fatal(constructor: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(klass={constructor})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("iterable", ["{1}", "{1: 2}", "'x'", "b'x'", "'abc'"])
def test_finite_literal_iterable_has_fatal_else(iterable: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\nfor item in {iterable}:\n pass\nelse:\n"
            " @task(unknown=True)\n def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_long_finite_loop_else_respects_replaced_decorator() -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task\nfor item in 'abc':\n"
        " task = replacement\nelse:\n @task(unknown=True)\n def other(c): ...\n"
        "@invoke.task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_unknown_help_key_suppresses_sibling_tasks() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(help={'missing': 'text'})\n"
            "def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "aliases", ["[1]", "(None,)", "{'ok', 1}", "{'ok': 1, 2: 3}", "b'bad'"]
)
def test_nonstring_literal_aliases_suppress_sibling_tasks(aliases: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(aliases={aliases})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        ("ValueError", []),
        ("(ValueError, OSError)", []),
        ("TypeError", ["build"]),
        ("Exception", ["build"]),
        ("(ValueError, TypeError)", ["build"]),
    ],
)
def test_constructor_typeerror_respects_handler_types(
    handler: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        "from invoke import task\ntry:\n @task(unknown=True)\n def broken(c): ...\n"
        f"except {handler}:\n pass\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == expected


def test_replaced_handler_name_may_catch_typeerror() -> None:
    tasks = parse_tasks(
        "from invoke import task\nValueError = TypeError\ntry:\n"
        " @task(unknown=True)\n def broken(c): ...\nexcept ValueError:\n pass\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    ("decorator", "signature", "fatal"),
    [
        ("task(klass=1)", "c", True),
        ("task(setup, pre=[setup])", "c", True),
        ("task(klass=Custom)", "c", False),
        ("task(**options)", "c", False),
        ("task(lambda c: None)", "c", True),
        ("task", "", True),
        ("task(optional=None)", "c", True),
        ("task()", "c", False),
        ("other", "c", False),
    ],
)
def test_incompatible_handler_checks_known_constructor_failures(
    decorator: str, signature: str, *, fatal: bool
) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task\ndef setup(c): ...\ntry:\n"
        f" @{decorator}\n def broken({signature}): ...\nexcept ValueError:\n pass\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ([] if fatal else ["setup", "build"])


def test_computed_default_does_not_prove_default_collision() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(default=computed)\ndef first(c): ...\n"
        "@task(default=True)\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["first", "build"]


def test_computed_alias_member_is_not_resolved() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(aliases=['literal', computed])\n"
        "def other(c): ...\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_string_loop_fatal_prefix_before_break() -> None:
    assert (
        parse_tasks(
            "from invoke import task\nfor item in 'x':\n @task(unknown=True)\n"
            " def broken(c): ...\n break\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("name", ["1", "1.5", "b'bad'", "['bad']", "{'bad': 1}"])
def test_truthy_nonstring_names_suppress_sibling_tasks(name: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task(name={name})\ndef broken(c): ...\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


def test_try_else_constructor_error_is_not_caught_by_handler() -> None:
    assert (
        parse_tasks(
            "from invoke import task\ntry:\n pass\nexcept TypeError:\n pass\nelse:\n"
            " @task(unknown=True)\n def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        ("ValueError", []),
        ("TypeError", []),
        ("AttributeError", ["build"]),
        ("Exception", ["build"]),
        ("(ValueError, AttributeError)", ["build"]),
    ],
)
def test_constructor_attributeerror_respects_handler_types(
    handler: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        "from invoke import task\ntry:\n @task(help='bad')\n def broken(c): ...\n"
        f"except {handler}:\n pass\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == expected


@pytest.mark.parametrize(
    ("subject", "pattern", "expected"),
    [
        ("1", "1", []),
        ("1", "1 | 2", []),
        ("True", "True", []),
        ("None", "None", []),
        ("'a'", "'a'", []),
        ("1", "2", ["build"]),
        ("computed", "1", ["build"]),
        ("1", "1 if flag", ["build"]),
        ("1", "1 if False", ["build"]),
    ],
)
def test_literal_match_guarantees_fatal_case(
    subject: str, pattern: str, expected: list[str]
) -> None:
    tasks = parse_tasks(
        f"from invoke import task\nmatch {subject}:\n case {pattern}:\n"
        "  @task(unknown=True)\n  def broken(c): ...\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == expected


def test_unreachable_match_capture_cannot_hide_fatal_case() -> None:
    assert (
        parse_tasks(
            "import invoke\nfrom invoke import task\nmatch 1:\n case 0 as task:\n"
            "  pass\n case 1:\n  @task(unknown=True)\n  def broken(c): ...\n"
            "@invoke.task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("loop", ["while False", "for item in []", "for item in ()"])
def test_zero_iteration_loop_preserves_decorator_import(loop: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n{loop}:\n task = replacement\n"
        "@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


@pytest.mark.parametrize(
    "outer",
    [
        "setattr(invoke, 'task', replacement) or identity",
        "(task := replacement) and identity",
    ],
)
def test_outer_decorator_mutation_precedes_lower_decorator(outer: str) -> None:
    tasks = parse_tasks(
        "import invoke\nfrom invoke import task, task as stable\n"
        "def replacement(**kwargs): return lambda f: f\n"
        f"@({outer})\n@{('task' if ':=' in outer else 'invoke.task')}(unknown=True)\n"
        "def other(c): ...\n@stable\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_handled_try_cannot_catch_deferred_collection_failure() -> None:
    assert (
        parse_tasks(
            "from invoke import task\ntry:\n @task(aliases=7)\n def broken(c): ...\n"
            "except TypeError:\n pass\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("option", ["iterable", "incrementable"])
def test_class_task_parser_membership_failure_suppresses_siblings(option: str) -> None:
    assert (
        parse_tasks(
            f"from invoke import task\n@task({option}=1)\nclass Broken:\n"
            " def __init__(self, c, item=False): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("cleanup", ["del broken", "broken = None"])
def test_removed_malformed_task_does_not_break_collection(cleanup: str) -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(aliases=7)\ndef broken(c): ...\n"
        f"{cleanup}\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_copied_malformed_task_still_breaks_collection() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(aliases=7)\ndef broken(c): ...\n"
            "saved = broken\ndel broken\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_finalizer_can_remove_deferred_collection_failure() -> None:
    tasks = parse_tasks(
        "from invoke import task\ntry:\n @task(aliases=7)\n def broken(c): ...\n"
        "except TypeError:\n pass\nfinally:\n del broken\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_argumentless_class_task_preserves_membership_option_siblings() -> None:
    tasks = parse_tasks(
        "from invoke import task\n@task(iterable=1)\nclass Other:\n"
        " def __init__(self, c): ...\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_caught_constructor_failure_does_not_create_malformed_task() -> None:
    tasks = parse_tasks(
        "from invoke import task\ntry:\n @task(aliases=7, unknown=True)\n"
        " def broken(c): ...\nexcept TypeError:\n pass\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_async_help_key_failure_suppresses_siblings() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(help={'missing': 'text'})\n"
            "async def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_class_global_malformed_task_suppresses_siblings() -> None:
    assert (
        parse_tasks(
            "from invoke import task\nclass Holder:\n global broken\n"
            " @task(aliases=7)\n def broken(c): ...\n@task\ndef build(c): ...\n"
        )
        == []
    )


def test_class_global_malformed_copy_survives_original_deletion() -> None:
    assert (
        parse_tasks(
            "from invoke import task\n@task(aliases=7)\ndef broken(c): ...\n"
            "class Holder:\n global saved\n saved = broken\ndel broken\n"
            "@task\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize("name", ["None", "False", "0", "0.0", "b''", "[]", "()", "{}"])
def test_falsy_literal_task_name_uses_function_name(name: str) -> None:
    tasks = parse_tasks(
        f"from invoke import task\n@task(name={name})\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_class_local_malformed_task_is_not_in_module_collection() -> None:
    tasks = parse_tasks(
        "from invoke import task\nclass Holder:\n @task(aliases=7)\n"
        " def broken(c): ...\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]


def test_removed_class_global_malformed_task_preserves_siblings() -> None:
    tasks = parse_tasks(
        "from invoke import task\nclass Holder:\n global broken\n @task(aliases=7)\n"
        " def broken(c): ...\ndel broken\n@task\ndef build(c): ...\n"
    )
    assert [task.name for task in tasks] == ["build"]
