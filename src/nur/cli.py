from __future__ import annotations

import sys
from pathlib import Path

import typer

import nur
from nur.discovery import discover
from nur.execution import run_direct
from nur.registry import AmbiguousTaskError, Registry, UnknownTaskError

__all__ = ["format_list", "main", "split_passthrough"]


app = typer.Typer(
    add_completion=False, context_settings={"help_option_names": ["-h", "--help"]}
)


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first ``--`` into task args and passthrough args."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return list(argv), []


def format_list(registry: Registry) -> str:
    """Render the discovered tasks grouped by provider prefix."""
    lines: list[str] = []
    for prefix, tasks in registry.groups().items():
        lines.append(prefix)
        for task in tasks:
            desc = f"  {task.description}" if task.description else ""
            lines.append(f"  {task.name}{desc}")
    return "\n".join(lines)


def _version_callback(value: bool) -> None:
    # Eager option: print the version and exit before the command body runs.
    if value:
        typer.echo(nur.__version__)
        raise typer.Exit(0)


@app.command()
def _run(
    ctx: typer.Context,
    task: str | None = typer.Argument(
        None, help="task to run, e.g. 'test' or 'make:test'; 'list' to list"
    ),
    version: bool = typer.Option(  # noqa: ARG001  (consumed by the eager callback)
        False,  # noqa: FBT003
        "-V",
        "--version",
        help="print version and exit",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Discover and run a project task, or launch the TUI when none is given."""
    # Passthrough args (everything after ``--``) are stashed on the context by main().
    extra: list[str] = ctx.obj["extra"] if ctx.obj else []
    cwd = Path.cwd()

    if task is None:
        # Launch the TUI immediately; it scans in the background. Do NOT call
        # discover() here — that is what used to block startup.
        from nur.tui.app import launch  # noqa: PLC0415

        raise typer.Exit(launch(cwd))

    registry = discover(cwd)

    if task == "list":
        typer.echo(format_list(registry))
        raise typer.Exit(0)

    try:
        resolved = registry.resolve(task)
    except AmbiguousTaskError as exc:
        typer.echo(
            f"nur: '{exc.name}' is ambiguous; candidates: {', '.join(exc.candidates)}",
            err=True,
        )
        raise typer.Exit(2) from exc
    except UnknownTaskError as exc:
        msg = f"nur: unknown task '{exc.query}'"
        if exc.suggestions:
            msg += f"; did you mean: {', '.join(exc.suggestions)}?"
        typer.echo(msg, err=True)
        raise typer.Exit(2) from exc

    raise typer.Exit(run_direct(resolved, extra, cwd))


def main(argv: list[str] | None = None) -> int:
    """Entry point: parse args with Typer, returning an int instead of exiting.

    Typer/Click raise ``SystemExit`` for help, version, and every command exit;
    catching it here keeps ``main``'s int contract so it stays usable from tests
    and embedders. Passthrough args are handed to the command via ``ctx.obj``.
    """
    raw = sys.argv[1:] if argv is None else argv
    left, extra = split_passthrough(raw)
    try:
        app(args=left, prog_name="nur", obj={"extra": extra})
    except SystemExit as exc:
        if exc.code is None:  # pragma: no cover - Typer always exits with an int code
            return 0
        return exc.code if isinstance(exc.code, int) else 2
    return 0  # pragma: no cover - Typer's app() always raises SystemExit
