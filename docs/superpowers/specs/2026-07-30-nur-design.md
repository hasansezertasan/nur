# Nur — Design Spec

**Date:** 2026-07-30
**Status:** Approved (implemented)

## 1. Overview

Nur is a **script discovery and execution engine** with a TUI-first interface. Run
`nur` inside a project and it scans the current directory for task-defining files
across multiple ecosystems, presents a unified, fuzzy-searchable picker grouped by
source, and runs the selected task via that source's native runner. Direct
invocation (`nur <name>`) is also supported for scripting and muscle memory.

### Motivation

Tools like `dela` and `mise` already discover-and-run tasks across formats. Nur's
reason to exist is **discovery and UX**: a polished, unified, fuzzy-searchable
interface across many task formats, with ergonomics that beat the existing tools.
Broad format support and being embeddable are secondary; the interactive experience
is the product.

### Goals

- Zero-config: work immediately in any supported project with no setup.
- Unified interface across heterogeneous task formats.
- Fast direct-run path suitable for scripts and aliases.
- Faithful execution: run each task exactly as its native runner would.

### Non-goals (v1)

- Config files / user settings.
- Monorepo, recursive, or walk-up discovery (CWD only).
- Editing, creating, or scaffolding tasks.
- User-defined / third-party providers.
- Version management or environment provisioning (that's `mise`'s job).

## 2. Scope

**In scope (v1) — task sources:**

- npm-family `package.json` `scripts` (npm / yarn / pnpm / bun)
- `Makefile` targets
- `pyproject.toml` — PDM scripts (`[tool.pdm.scripts]`)
- `pyproject.toml` — poethepoet tasks (`[tool.poe.tasks]`)
- `justfile` recipes
- `Taskfile.yml` tasks

**In scope — interaction:**

- TUI picker (three-pane: list / details / execution output) when run with no task
  argument, with tasks executed inside the TUI.
- Direct invocation: `nur <name>` and `nur <prefix>:<name>`.
- Argument passthrough via `--`.
- `nur list` — plain, scriptable listing (no TUI).
- `nur --version` / `-V` and `nur --help` / `-h`.

**Discovery scope:** current working directory only. Nur does not walk up the tree or
descend into subdirectories.

## 3. Architecture

A small core orchestrates a set of **providers**, one per source family. The core is
responsible for detection, aggregation, name/collision resolution, and dispatch. Each
provider encapsulates one source format.

### 3.1 Provider interface

```python
class Task:
    name: str  # task name as defined in the source, e.g. "test"
    prefix: str  # owning provider prefix, e.g. "npm"
    description: str | None  # human description if the source provides one
    definition: str  # raw command/body, for preview (may be multi-line)
    source_file: str  # relative path to the file it came from

    def run_argv(self, extra_args: list[str]) -> list[str]:
        """Build the argv to execute this task, appending passthrough args."""


class Provider(Protocol):
    prefix: str  # "npm" | "make" | "pdm" | "poe" | "just" | "task"

    def detect(self, cwd: Path) -> bool:
        """True if this source is present in cwd."""

    def discover(self, cwd: Path) -> list[Task]:
        """Parse or delegate to produce the task list. Never raises for a
        malformed source: logs a warning and returns what it can (or [])."""
```

### 3.2 Pipeline

```
detect (all providers)
  → discover (detected providers)
    → aggregate into a registry (keyed by prefix:name, with bare-name index)
      → TUI picker  (no task arg)
      → direct run  (task arg given → resolve → dispatch)
        → subprocess (inherited stdio) → exit with child's return code
```

Adding a new source later means adding one `Provider` implementation and registering
it. Nothing else in the core changes.

## 4. Discovery & Parsing (hybrid strategy)

Structured formats are parsed natively (zero external dependency, fast). Formats with
awkward grammars are discovered by delegating to the tool's own machine-readable list
command (authoritative, and the tool must be installed to run the task anyway).

| Provider | File | Trigger | Method | Runner invocation |
|----------|------|---------|--------|-------------------|
| `npm`  | `package.json`  | file exists + has `scripts` | native (`json`)             | `<pm> run <name>` (pm resolved from lockfile) |
| `make` | `Makefile`      | file exists                 | native text parse (never runs `make` — see security note) | `make <target>` |
| `pdm`  | `pyproject.toml`| `[tool.pdm.scripts]` present| native (`tomllib`)          | `pdm run <name>` |
| `poe`  | `pyproject.toml`| `[tool.poe.tasks]` present  | native (`tomllib`)          | `poe <name>` |
| `just` | `justfile`      | file exists                 | delegate (`just --dump --dump-format json`) | `just <recipe>` |
| `task` | `Taskfile.yml`  | file exists                 | native YAML parse (never runs `task` — see security note) | `task <name>` |

Notes:

- **npm package-manager resolution:** choose runner by lockfile presence in this
  order — `bun.lock`/`bun.lockb` → `bun`, `pnpm-lock.yaml` → `pnpm`, `yarn.lock` →
  `yarn`, `package-lock.json` → `npm`. Default to `npm` if no lockfile is found.
- **Makefile — safe discovery (security):** Nur parses the `Makefile` as **text**
  and never invokes `make` for discovery. `make`'s database dump (`make -pRrq`)
  still evaluates `$(shell …)` / `!=` assignments while reading the file, so
  delegating discovery would run arbitrary repo-controlled commands on `nur` /
  `nur list`. Text parsing extracts target names and the `target: ## description`
  convention, filtering special targets (`.PHONY`, pattern rules `%`, dot-prefixed).
  Tradeoff: `include`d or computed targets are not resolved.
- **npm/pnpm/yarn/bun passthrough:** the emitted `argv_base` forwards extra args
  after a `--` separator (`npm run <script> -- <args>`), which npm requires and the
  others accept.
- **Taskfile — safe discovery (security):** Nur reads the Taskfile's YAML
  directly (PyYAML `safe_load`) and never runs `task`. `task --list` evaluates
  dynamic `sh:` variables while compiling the file, so delegating discovery
  would run repo-controlled commands on `nur` / `nur list`. Accept both
  `Taskfile.yml` and `Taskfile.yaml`; `internal: true` tasks are skipped.
  Tradeoff: `includes:` and generated tasks are not resolved.
- **Delegated provider, tool missing:** the provider is skipped during discovery with
  a quiet one-line note. It does not error the whole run or blank the list.
- Both `pdm` and `poe` may coexist in the same `pyproject.toml`; each is an
  independent provider keyed by its own prefix.

## 5. Execution Model

Nur has two execution paths depending on how the task was launched.

### 5.1 In-TUI execution (picker path)

When a task is launched from the TUI, its output is **captured and streamed into the
execution pane** (right-bottom) rather than tearing the app down. The list and detail
panes stay visible so the user can run the next task after this one finishes.

- The task is spawned with its `stdout`/`stderr` captured (combined) and streamed
  line-by-line into a scrollable output pane. A header line shows the resolved
  command; a status line shows running / exited(code) state.
- **No interactive input in v1:** the child's `stdin` is not connected to the user.
  Tasks that expect interactive prompts are out of scope for the in-TUI path — run
  those via the direct-run path (§5.2) instead. Because the child is not attached to a
  TTY, runners that gate color on a TTY will emit plain output; that is an accepted
  v1 tradeoff.
- `Ctrl-C` interrupts a running task (scoped to the child) without quitting Nur.
- Only one task runs at a time in v1; launching another while one runs is disabled
  until it exits (no concurrent execution).

### 5.2 Direct-run execution (CLI path)

`nur <name>` / `nur <prefix>:<name>` never starts the TUI. The task runs as a
subprocess with **inherited stdio** (`stdin`/`stdout`/`stderr`), giving full native
TTY passthrough, and Nur exits with the child's exit code.

### 5.3 Common rules

- **Argument passthrough:** everything after `--` is appended to the built argv.
  Example: `nur npm:test -- --watch` → `pnpm run test --watch`.
- Nur adds no shell wrapping beyond what each runner itself requires; it execs the
  runner directly with an argv vector (no intermediate shell) to avoid quoting
  surprises.
- The task runs in the current working directory.

## 6. CLI Surface

| Command | Behavior |
|---------|----------|
| `nur` | Open the TUI picker. |
| `nur <name>` | Run the task if `<name>` is unique across all sources. |
| `nur <prefix>:<name>` | Run the explicitly qualified task, e.g. `nur make:test`. |
| `nur <name> -- <args…>` | Run with passthrough args appended. |
| `nur list` | Print a plain, grouped listing (scriptable, no TUI). |
| `nur --version` / `-V` | Print Nur's version and exit. |
| `nur --help` / `-h` | Print usage and exit. |

**Collision handling:** task names are addressed as `prefix:name`. A bare `nur <name>`
runs only when exactly one provider defines that name. If the name is ambiguous, Nur
exits with a clear error listing the qualified candidates (e.g. `npm:test`,
`make:test`) so the user can re-run with a prefix.

**Unknown task:** exit with an error and suggest the closest matches by string
similarity.

## 7. TUI (Textual)

Built with [Textual](https://textual.textualize.io/). **Lazy-imported** — Textual is
only imported on the picker path, so `nur <name>` direct runs never pay the import
cost. The layout and keybindings follow the keyboard-driven, multi-pane TUI
convention (vim-style navigation, `Tab` to cycle panes).

### 7.1 Layout — three panes

```
┌ nur ─────────────────────────────────────────────────┐
│ / filter: te▌                                         │
├──────────────────────┬────────────────────────────────┤
│ TASKS                │ DETAILS                         │
│ npm                  │ npm:test                        │
│  ❯ test              │ run unit tests                  │
│    test:watch        │ $ pnpm run test                 │
│ make                 │ from package.json               │
│    test              ├────────────────────────────────┤
│ pdm                  │ OUTPUT                          │
│    typecheck         │ $ pnpm run test                 │
│                      │ PASS  12 passed in 0.4s         │
│                      │ ▌ (running…)                    │
├──────────────────────┴────────────────────────────────┤
│ r/↵ run · Tab pane · j/k move · / filter · ? help · q │
└─────────────────────────────────────────────────────────┘
```

- **Left — task list**, grouped under source headers (`npm`, `make`, …). Current
  selection highlighted with `❯`.
- **Right-top — details** for the selected task: qualified name, description, the
  resolved command (`$ …`), the originating source file, and — for multi-line
  definitions — the full task body.
- **Right-bottom — execution output**: streams the running task's output (see §5.1),
  scrollable, with a running / exited(code) status line.
- **Top:** fuzzy filter box. **Bottom:** keybinding hint bar.

### 7.2 Interaction (vim-style keys)

- **Panes:** `Tab` cycles focus between the three panes; the focused pane is
  highlighted.
- **Navigation:** `j`/`k` (and `↑`/`↓`) move the selection in the focused list; the
  output pane scrolls with the same keys when focused.
- **Fuzzy filter** on task name **and** description: `/` focuses the filter box.
- **Run:** `r` or `Enter` runs the selected task in the execution pane (§5.1).
- **Interrupt:** `Ctrl-C` interrupts a running task (scoped to the child) without
  quitting Nur.
- **Help:** `?` toggles a keybinding help overlay.
- **Quit:** `q` (or `Esc`).

### 7.3 Narrow-terminal fallback

Below a width threshold the right column collapses: the detail + output panes stack
below the list (or hide the detail and keep output while a task runs), so Nur remains
usable in small terminals.

### 7.4 Theme

Adopt the terminal's own colors; use source-colored accents for group headers /
badges. No custom color scheme required in v1.

## 8. Error Handling

| Situation | Behavior |
|-----------|----------|
| Malformed source file | Skip that provider, emit a warning, continue with the rest. One bad file never blanks the whole list. |
| Delegated tool not installed | Skip that provider during discovery with a quiet note. On a direct-run request for such a task, explain which tool to install. |
| Unknown task name | Error + closest-match suggestions. |
| Ambiguous bare name | Error listing the qualified candidates. |
| Task runner exits non-zero | Direct-run: Nur propagates the child's exit code. In-TUI: the exit code is shown in the execution pane's status line; no wrapping or swallowing on either path. |
| No tasks discovered | Friendly message stating no supported task files were found in the CWD. |

Errors are never silently swallowed; every skipped provider or failed discovery
surfaces a visible note.

## 9. Testing (exhaustive & comprehensive)

Tooling: `pytest`, run via `uv`. Coverage is measured with a hard floor enforced in
CI (target: high line + branch coverage; fail the build below the floor).

### 9.1 Provider unit tests

- Each provider gets fixture project directories containing representative source
  files (typical, minimal, and adversarial).
- Assert: correct set of tasks discovered, correct names/descriptions/definitions,
  and correct `run_argv()` output (including passthrough args).
- npm: cover every package-manager lockfile permutation and the no-lockfile default.
- make: `## ` description extraction; correct filtering of `.PHONY`, pattern rules,
  and dot-prefixed special targets.
- pdm/poe: presence/absence of each `[tool.*]` table; coexistence in one file.
- just/task: delegated JSON output parsed correctly; tool-missing path skips cleanly.

### 9.2 Core tests

- Aggregation across multiple providers into the registry.
- Collision resolution: bare unique name runs; ambiguous bare name errors with the
  correct candidate list; `prefix:name` always resolves.
- Unknown-name closest-match suggestions.
- Argument passthrough assembly (`-- <args>`).

### 9.3 CLI tests

- Argument parsing for every command in §6.
- `--version` / `--help` output and exit codes.
- Exit-code propagation from the child process.
- `nur list` output format is stable and scriptable.

### 9.4 TUI interaction tests

- Driven by Textual's `Pilot` test harness.
- Filtering narrows the list; `j`/`k` navigation moves the selection; the detail pane
  updates to match the selection; `Tab` cycles pane focus; `r`/`Enter` resolves the
  intended task; `?` toggles help; `q`/`Esc` quit.
- **Execution pane:** launching a task streams its captured output into the pane; the
  status line transitions running → exited(code); `Ctrl-C` interrupts a running child;
  a second launch is blocked while one is running.
- Narrow-terminal fallback renders the collapsed/stacked layout.

### 9.5 Edge cases

- Malformed / empty source files.
- Empty sources (e.g. `package.json` with no `scripts`).
- Missing delegated runners.
- Duplicate names across sources.
- Unicode, whitespace, and unusual characters in task names.

### 9.6 End-to-end tests

True end-to-end tests that **actually execute real runners** against sandboxed fixture
repositories (not merely asserting the built argv) — e.g. a fixture with a real
`Makefile` / `package.json` whose task writes a sentinel file, then assert the effect
and the propagated exit code. Gated to run only when the corresponding runner is
available on the test machine / CI image.

## 10. Distribution & Tech Stack

- **Language:** Python ≥ 3.14.
- **TUI:** Textual (lazy-imported).
- **Tooling:** `uv` for env/build/run; `ruff` for lint/format; `pytest` for tests;
  full type/quality battery (mypy, pyright, ty, pyrefly, pylint, vulture, slotscheck).
- **Parsing deps:** stdlib `json` and `tomllib` for native providers; **PyYAML**
  for safe Taskfile parsing (delegation would execute the file — see §4).
- **Install:** via `uv tool install` / `pipx`. Single-binary distribution is a
  non-goal for v1 (acknowledged tradeoff of the Python choice).

## 11. Open Questions / Deferred

- Config file (`[tool.nur]` or `.nurrc`) to enable/disable providers — deferred.
- Monorepo / recursive discovery — deferred.
- User-defined providers — deferred.
- Task caching for faster repeated discovery — deferred; measure first.
