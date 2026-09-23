from nur.core.models import Task


def test_qualified_name() -> None:
    t = Task(name="test", prefix="npm", argv_base=("pnpm", "run", "test"))
    assert t.qualified_name == "npm:test"


def test_run_argv_appends_extra_args() -> None:
    t = Task(name="test", prefix="npm", argv_base=("pnpm", "run", "test"))
    assert t.run_argv(["--watch"]) == ["pnpm", "run", "test", "--watch"]


def test_run_argv_no_extra_args() -> None:
    t = Task(name="build", prefix="make", argv_base=("make", "build"))
    assert t.run_argv() == ["make", "build"]


def test_run_argv_inserts_passthrough_separator_only_with_extra_args() -> None:
    t = Task(
        name="test",
        prefix="npm",
        argv_base=("npm", "run", "test"),
        passthrough_prefix=("--",),
    )
    # No extra args -> no separator.
    assert t.run_argv() == ["npm", "run", "test"]
    # Extra args -> separator inserted before them (npm needs `npm run test -- ...`).
    assert t.run_argv(["--watch"]) == ["npm", "run", "test", "--", "--watch"]
