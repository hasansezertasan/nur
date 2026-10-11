import json

import pytest

from nur.core.discovery import discover
from nur.core.providers.workspace import WorkspaceProvider, member_scripts


def _pkg(tmp_path, rel, scripts, name=None) -> None:
    member = tmp_path / rel
    member.mkdir(parents=True, exist_ok=True)
    data = {"scripts": scripts}
    if name is not None:
        data["name"] = name
    (member / "package.json").write_text(json.dumps(data))


def _pnpm(tmp_path, *, members=True) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/*'\n  - 'packages/*'\n"
    )
    (tmp_path / "pnpm-lock.yaml").write_text("")
    if members:
        _pkg(
            tmp_path, "apps/web", {"build": "vite build", "test": "vitest"}, name="web"
        )
        _pkg(tmp_path, "packages/ui", {"build": "tsc"}, name="ui")


def test_detect_true_for_workspace_markers(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages: []\n")
    assert WorkspaceProvider().detect(tmp_path)


def test_detect_true_for_lerna(tmp_path) -> None:
    (tmp_path / "lerna.json").write_text("{}")
    assert WorkspaceProvider().detect(tmp_path)


def test_detect_true_for_package_json_workspaces(tmp_path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
    assert WorkspaceProvider().detect(tmp_path)


def test_detect_false_and_silent_without_markers(tmp_path, caplog) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"a": "b"}}))
    assert not WorkspaceProvider().detect(tmp_path)
    assert not WorkspaceProvider().detect(tmp_path / "missing")
    (tmp_path / "package.json").write_text("{ nope")
    assert not WorkspaceProvider().detect(tmp_path)
    assert caplog.records == []


def test_discover_pnpm_members(tmp_path) -> None:
    _pnpm(tmp_path)
    tasks = {t.name: t for t in WorkspaceProvider().discover(tmp_path)}
    assert set(tasks) == {"web:build", "web:test", "ui:build"}
    assert tasks["web:build"].argv_base == ("pnpm", "--filter", "web", "run", "build")
    assert tasks["web:build"].qualified_name == "workspace:web:build"
    assert tasks["web:build"].definition == "vite build"
    assert tasks["web:build"].source_file == "apps/web/package.json"
    assert tasks["web:build"].run_argv(["--watch"]) == [
        "pnpm",
        "--filter",
        "web",
        "run",
        "build",
        "--",
        "--watch",
    ]


@pytest.mark.parametrize(
    ("lockfile", "argv"),
    [
        ("package-lock.json", ("npm", "run", "build", "--workspace", "web")),
        ("yarn.lock", ("yarn", "workspace", "web", "run", "build")),
        ("bun.lock", ("bun", "run", "--filter", "web", "build")),
    ],
)
def test_discover_runner_per_lockfile(tmp_path, lockfile, argv) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
    (tmp_path / lockfile).write_text("")
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.argv_base == argv


def test_discover_npm_workspaces_list_form(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"private": True, "workspaces": ["apps/*"]})
    )
    (tmp_path / "package-lock.json").write_text("")
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.argv_base == ("npm", "run", "build", "--workspace", "web")


def test_discover_npm_workspaces_dict_form(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": {"packages": ["apps/*"]}})
    )
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.name == "web:build"


def test_discover_lerna_packages(tmp_path) -> None:
    (tmp_path / "lerna.json").write_text(json.dumps({"packages": ["apps/*"]}))
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.argv_base == ("lerna", "run", "build", "--scope", "web")


def test_discover_lerna_defaults_to_packages_glob(tmp_path) -> None:
    (tmp_path / "lerna.json").write_text(json.dumps({"version": "0.0.0"}))
    _pkg(tmp_path, "packages/ui", {"build": "x"}, name="ui")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.name == "ui:build"


def test_discover_lerna_use_workspaces(tmp_path) -> None:
    (tmp_path / "lerna.json").write_text(json.dumps({"useWorkspaces": True}))
    (tmp_path / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.name == "web:build"


def test_discover_unnamed_member_uses_path_selector(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    _pkg(tmp_path, "apps/web", {"build": "x"})
    (task,) = WorkspaceProvider().discover(tmp_path)
    assert task.name == "apps/web:build"
    assert task.argv_base == ("pnpm", "--filter", "apps/web", "run", "build")


def test_discover_skips_members_without_usable_scripts(tmp_path, caplog) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
    _pkg(tmp_path, "apps/empty", {}, name="empty")
    noscripts = tmp_path / "apps" / "noscripts"
    noscripts.mkdir(parents=True)
    (noscripts / "package.json").write_text(json.dumps({"name": "noscripts"}))
    (tmp_path / "apps/broken").mkdir(parents=True)
    (tmp_path / "apps/broken" / "package.json").write_text("{ nope")
    assert WorkspaceProvider().discover(tmp_path) == []
    assert any("package.json" in r.message for r in caplog.records)


def test_discover_honors_negations_and_braces(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'packages/{a,b}'\n  - '!packages/b'\n"
    )
    _pkg(tmp_path, "packages/a", {"build": "x"}, name="a")
    _pkg(tmp_path, "packages/b", {"build": "x"}, name="b")
    tasks = {t.name for t in WorkspaceProvider().discover(tmp_path)}
    assert tasks == {"a:build"}


def test_discover_confines_traversal_to_cwd(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n"
        "  - 'apps/*'\n"
        "  - 'apps/**'\n"
        "  - '../outside/*'\n"
        "  - '/absolute/*'\n"
    )
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    (tmp_path / "apps/web" / "node_modules").mkdir()
    (tmp_path / "apps/web" / "node_modules" / "dep").mkdir()
    (tmp_path / "apps/web" / "node_modules" / "dep" / "package.json").write_text(
        json.dumps({"name": "dep", "scripts": {"evil": "x"}})
    )
    tasks = {t.name for t in WorkspaceProvider().discover(tmp_path)}
    assert tasks == {"web:build"}


def test_discover_recursive_glob_finds_nested_members(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/**'\n")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    _pkg(tmp_path, "packages/a", {"build": "x"}, name="a")
    _pkg(tmp_path, "packages/a/b", {"test": "y"}, name="b")
    tasks = {t.name for t in WorkspaceProvider().discover(tmp_path)}
    assert tasks == {"a:build", "b:test"}


def test_discover_recursive_glob_prunes_skipped_dirs(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - '**'\n")
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "package.json").write_text(
        json.dumps({"name": "hooks", "scripts": {"evil": "x"}})
    )
    tasks = {t.name for t in WorkspaceProvider().discover(tmp_path)}
    assert tasks == {"web:build"}


def _symlink_dir(link, target) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # pragma: no cover - e.g. Windows without symlink rights
        pytest.skip("directory symlinks unsupported here")


def test_discover_recursive_glob_follows_in_root_symlinks(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/**'\n")
    (tmp_path / "pnpm-lock.yaml").write_text("")
    _pkg(tmp_path, "real/a", {"build": "x"}, name="a")
    _symlink_dir(tmp_path / "packages", tmp_path / "real")
    # A cycle back to the root and a link leaving it must both be pruned.
    _symlink_dir(tmp_path / "real" / "a" / "loop", tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _pkg(outside, "ext", {"evil": "x"}, name="ext")
    _symlink_dir(tmp_path / "real" / "ext", outside / "ext")
    tasks = {t.name for t in WorkspaceProvider().discover(tmp_path)}
    assert tasks == {"a:build"}


def test_brace_edge_cases_match_nothing(tmp_path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'apps/{unclosed'\n  - 'apps/{a/{b,c}}'\n"
    )
    _pkg(tmp_path, "apps/web", {"build": "x"}, name="web")
    assert WorkspaceProvider().discover(tmp_path) == []


def test_rel_posix_guards(tmp_path) -> None:
    from nur.core.providers.workspace import _rel_posix

    assert _rel_posix(tmp_path, tmp_path.parent / "outside") is None


def test_rel_posix_unresolvable_returns_none(tmp_path, monkeypatch) -> None:
    from nur.core.providers.workspace import _rel_posix

    def boom(self):
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.resolve", boom)
    assert _rel_posix(tmp_path, tmp_path / "x") is None


def test_recursive_match_unresolvable_paths_are_skipped(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from nur.core.providers.workspace import _recursive_match

    _pkg(tmp_path, "packages/a", {"build": "x"}, name="a")
    real_resolve = Path.resolve

    def resolve_root_only(self, *args, **kwargs):
        if self != tmp_path:
            msg = "nope"
            raise OSError(msg)
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr("pathlib.Path.resolve", resolve_root_only)
    assert _recursive_match(tmp_path, "packages/**") == {}

    def boom(self, *args, **kwargs):
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.resolve", boom)
    assert _recursive_match(tmp_path, "packages/**") == {}


def test_match_glob_errors_are_skipped(tmp_path, monkeypatch) -> None:
    from nur.core.providers.workspace import _match

    def boom(self, pattern):
        msg = "nope"
        raise OSError(msg)

    monkeypatch.setattr("pathlib.Path.glob", boom)
    assert _match(tmp_path, "apps/*") == {}


def test_discover_skips_root_itself(tmp_path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "root", "scripts": {"build": "x"}, "workspaces": ["."]})
    )
    assert WorkspaceProvider().discover(tmp_path) == []


def test_discover_malformed_workspace_config_warns(tmp_path, caplog) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text("packages: [unclosed\n")
    assert WorkspaceProvider().discover(tmp_path) == []
    assert any("pnpm-workspace.yaml" in r.message for r in caplog.records)


def test_discover_no_members_returns_empty(tmp_path) -> None:
    _pnpm(tmp_path, members=False)
    assert member_scripts(tmp_path) == []


def test_discover_member_tasks_resolve_by_member_name(tmp_path) -> None:
    _pnpm(tmp_path)
    registry = discover(tmp_path)
    assert "workspace:web:build" in {t.qualified_name for t in registry.all()}
    # `partition(":")` splits the provider prefix, so `web:build` matches the
    # bare task name directly — no ambiguity across members.
    assert registry.resolve("web:build").argv_base == (
        "pnpm",
        "--filter",
        "web",
        "run",
        "build",
    )
    assert registry.resolve("workspace:ui:build").argv_base == (
        "pnpm",
        "--filter",
        "ui",
        "run",
        "build",
    )
