from typing import TYPE_CHECKING

import pytest

from nur.core.discovery import discover
from nur.core.providers.invoke import InvokeProvider, parse_tasks

if TYPE_CHECKING:
    from pathlib import Path


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
    ("name", "expected"),
    [("_build_all_", "_build-all_"), ("_", "_"), ("a_.b_c", "a_.b-c")],
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
    "reimport",
    [
        "import invoke",
        "import invoke as inv",
        "import invoke.tasks as inv",
        "from invoke import tasks as inv",
        "from invoke import task as restored",
        "from invoke.tasks import task as restored",
    ],
)
def test_mutated_modules_remain_untrusted_after_imports(reimport: str) -> None:
    decorator = (
        "restored"
        if "restored" in reimport
        else "inv.task"
        if "inv" in reimport.split()
        else "invoke.task"
    )
    assert (
        parse_tasks(
            "import invoke\ninvoke.task = lambda f: f\n"
            f"{reimport}\n@{decorator}\ndef build(c): ...\n"
        )
        == []
    )


@pytest.mark.parametrize(
    "body",
    [
        "invoke.task = lambda f: f",
        "invoke.tasks.task = lambda f: f",
        "import invoke as inv\n inv.task = lambda f: f",
        "if True:\n  invoke.task = lambda f: f",
        "class Nested:\n  invoke.task = lambda f: f",
        'setattr(invoke, "task", other)',
    ],
)
def test_executed_class_mutations_invalidate_modules(body: str) -> None:
    assert (
        parse_tasks(
            f"import invoke\nclass Helper:\n {body}\n@invoke.task\ndef build(c): ...\n"
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
