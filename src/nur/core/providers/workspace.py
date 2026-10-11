"""Discover workspace member scripts by parsing configs, without traversal tools."""

from __future__ import annotations

import json
import logging
from operator import itemgetter
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import yaml

from nur.core.models import Task
from nur.core.providers._package_manager import LOCKFILES, resolve_pm

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["WorkspaceProvider", "member_scripts"]

log = logging.getLogger("nur")

# Directories that are never workspace members, even when a glob matches them.
_SKIPPED_PARTS = frozenset({"node_modules", ".git"})


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        log.warning("nur: skipping %s (%s)", path.name, exc)
        return None


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _package_globs(cwd: Path) -> list[str]:
    """Collect member globs from pnpm, package.json, and lerna configs."""
    patterns: list[str] = []
    pnpm = _read_yaml(cwd / "pnpm-workspace.yaml")
    if isinstance(pnpm, dict):
        patterns.extend(_str_list(pnpm.get("packages")))
    root = _read_json(cwd / "package.json")
    workspaces = root.get("workspaces") if isinstance(root, dict) else None
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    patterns.extend(_str_list(workspaces))
    lerna = _read_json(cwd / "lerna.json")
    if isinstance(lerna, dict):
        if lerna.get("useWorkspaces") is True:
            pass  # package.json workspaces above already apply
        elif "packages" in lerna:
            patterns.extend(_str_list(lerna.get("packages")))
        else:
            patterns.append("packages/*")  # lerna's default when unset
    return patterns


def _expand_braces(pattern: str) -> list[str]:
    """Expand one level of ``{a,b}`` alternation; nested braces are left alone."""
    start = pattern.find("{")
    if start == -1:
        return [pattern]
    end = pattern.find("}", start + 1)
    if end == -1:
        return [pattern]
    inner = pattern[start + 1 : end]
    if "{" in inner or "}" in inner:
        return [pattern]
    head, tail = pattern[:start], pattern[end + 1 :]
    return [f"{head}{choice}{tail}" for choice in inner.split(",")]


def _rel_posix(cwd: Path, path: Path) -> str | None:
    """Return ``path`` relative to ``cwd`` in posix form, or None if outside."""
    try:
        resolved = path.resolve()
    except OSError:
        return None
    try:
        rel = resolved.relative_to(cwd.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _match(cwd: Path, pattern: str) -> dict[str, Path]:
    """Map member rel-posix paths to directories for one include pattern."""
    found: dict[str, Path] = {}
    if PurePosixPath(pattern).is_absolute():
        return found
    for expanded in _expand_braces(pattern):
        try:
            candidates = list(cwd.glob(expanded))
        except OSError:
            continue
        except ValueError:
            continue
        for candidate in candidates:
            rel = _rel_posix(cwd, candidate)
            if (
                rel is None
                or rel == "."
                or not candidate.is_dir()
                or not (candidate / "package.json").is_file()
                or _SKIPPED_PARTS & set(PurePosixPath(rel).parts)
            ):
                continue
            found.setdefault(rel, candidate)
    return found


def _members(cwd: Path, patterns: list[str]) -> list[tuple[str, Path]]:
    """Resolve member ``(rel-posix, path)`` pairs, sorted, minus `!` excludes."""
    included: dict[str, Path] = {}
    excluded: set[str] = set()
    for pattern in patterns:
        if pattern.startswith("!"):
            excluded.update(_match(cwd, pattern[1:]))
        else:
            included.update(_match(cwd, pattern))
    return sorted(
        ((rel, path) for rel, path in included.items() if rel not in excluded),
        key=itemgetter(0),
    )


def _selector(member: Path, rel: str) -> str | None:
    """Return the `--filter`-style selector: package `name`, else its path."""
    data = _read_json(member / "package.json")
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name
    return rel


def _argv(pm: str, selector: str, script: str) -> tuple[str, ...]:
    if pm == "pnpm":
        return ("pnpm", "--filter", selector, "run", script)
    if pm == "yarn":
        return ("yarn", "workspace", selector, "run", script)
    if pm == "bun":
        return ("bun", "run", "--filter", selector, script)
    if pm == "lerna":
        return ("lerna", "run", script, "--scope", selector)
    return ("npm", "run", script, "--workspace", selector)


def _runner(cwd: Path) -> str:
    # Lockfiles decide the runner; a bare lerna.json (no lockfile) means the
    # project delegates through lerna rather than npm workspaces.
    for lockfile, _ in LOCKFILES:
        if (cwd / lockfile).exists():
            return resolve_pm(cwd)
    if (cwd / "lerna.json").is_file():
        return "lerna"
    return "npm"


def member_scripts(cwd: Path) -> list[Task]:
    """Discover workspace member scripts without executing anything.

    Globs come from ``pnpm-workspace.yaml`` (``packages``), ``package.json``
    (``workspaces``), and ``lerna.json`` (``packages``, defaulting to
    ``packages/*`` unless ``useWorkspaces`` defers to ``package.json``).
    Matching stays confined to ``cwd``: absolute patterns, escapes, and
    ``node_modules``/``.git`` never become members, and only directories with
    their own ``package.json`` qualify. Member scripts are the same static
    JSON the ``npm`` provider reads; running delegates to the
    lockfile-resolved package manager scoped to the member.
    """
    members = _members(cwd, _package_globs(cwd))
    if not members:
        return []
    pm = _runner(cwd)
    tasks: list[Task] = []
    for rel, member in members:
        selector = _selector(member, rel)
        if selector is None:
            continue
        data = _read_json(member / "package.json")
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if not isinstance(scripts, dict):
            continue
        # Members are deduplicated by path and scripts by key, so names here
        # are unique by construction.
        for script, command in scripts.items():
            tasks.append(
                Task(
                    name=f"{selector}:{script}",
                    prefix="workspace",
                    argv_base=_argv(pm, selector, script),
                    definition=str(command),
                    source_file=f"{rel}/package.json",
                    # All five runners accept `--` to forward args to the script.
                    passthrough_prefix=("--",),
                )
            )
    return tasks


class WorkspaceProvider:
    prefix = "workspace"

    def detect(self, cwd: Path) -> bool:
        if (cwd / "pnpm-workspace.yaml").is_file() or (cwd / "lerna.json").is_file():
            return True
        # Silent on absent/malformed files: detect runs on every project, so
        # (unlike discover) it must never warn. UnicodeError and
        # JSONDecodeError subclass ValueError.
        try:
            data = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
        except OSError:
            return False
        except ValueError:
            return False
        return isinstance(data, dict) and "workspaces" in data

    def discover(self, cwd: Path) -> list[Task]:
        return member_scripts(cwd)
