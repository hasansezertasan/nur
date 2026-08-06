---
status: accepted
date: 2026-08-06
decision-makers: [hasansezertasan]
consulted: []
informed: []
---

# Provider selection criteria: static discovery, never execution

## Context and Problem Statement

`nur` discovers runnable tasks by reading a project's files and lets the user run
them from a TUI/CLI. Each provider parses one source-file format into a list of
tasks (name, run command, description). As the ecosystem of task runners is large
and still growing, we repeatedly face the same question: **which task-runner /
script formats should become providers, and which should we decline?**

Without a written rule this gets re-litigated every time a new tool appears, and
it is tempting to chase raw popularity (e.g. Gradle, GitHub Actions) even when
those formats cannot be supported without violating `nur`'s core promise.

## Decision Drivers

- **Safety / predictability** — running `nur` to *list* tasks must never execute
  the project's own task code (recipe/target/script bodies) or otherwise run
  arbitrary project-controlled commands. Only the user's explicit choice runs
  anything.
- **Reliability** — a provider should enumerate tasks as completely and correctly
  as it can *without* violating the safety rule; where full enumeration would
  require executing project code or resolving remote state, a safe subset is
  acceptable.
- **Scope** — discovery is limited to the current directory; providers parse a
  single well-known source file.
- **Value** — real-world adoption and distinct ecosystem coverage, weighed
  *after* the constraints above, not before.

## Considered Options

1. **Support any sufficiently popular task runner**, shelling out to the tool
   (`gradle tasks`, `tox -l`, `pre-commit run`, …) to enumerate tasks when static
   parsing is hard.
2. **Static discovery only** — a format qualifies only if its tasks can be
   enumerated by parsing a single source file in the current directory, without
   executing code. Rank qualifying candidates by adoption × ecosystem fit.
3. **Declarative-config formats only** (JSON/TOML/YAML/INI), excluding
   markdown/prose task definitions.

## Decision Outcome

Chosen option: **"Static discovery only"** (option 2).

A candidate format becomes a provider **only if all** of the following hold:

1. Tasks are discoverable by **parsing a source file** in the current directory.
2. **Hard rule — listing never executes the project's task code.** Discovery must
   not run recipe/target/script bodies or evaluate project-controlled expressions.
   Pure file parsing is the default and strongly preferred; invoking a runner's
   own *parse-only* dump (e.g. `just --dump`) is tolerated only when it does not
   execute recipe bodies, and is treated as a deviation to be retired (see below).
3. The file yields at least a task **name** and a **run command** (a description
   is a bonus). Enumeration may be a **safe subset** — completeness yields to the
   hard rule.

Formats that clear the bar are then prioritized by adoption and by covering an
ecosystem `nur` does not yet reach. Markdown/prose formats are allowed (we already
ship `xc`), so option 3 is rejected as too narrow. Option 1 is rejected as a
general strategy: shelling out to *enumerate* tasks by executing project build
logic (e.g. `make -pRrq`, which evaluates `$(shell …)` while reading) breaks the
hard rule — which is exactly why the `make` provider text-parses instead.

### Known deviations and partial support (current state)

The rule above describes the target invariant. Two existing supported providers
qualify it, and are recorded here so the ADR matches reality:

- **`just` shells out (grandfathered).** `JustProvider.discover()` runs
  `just --dump --dump-format json` (a parse-only dump, with a 5s timeout that
  skips gracefully when `just` is absent) rather than parsing the `justfile`
  grammar itself. This satisfies the hard rule (recipe bodies are not executed)
  but violates the "pure file parse, no subprocess/tool dependency" preference.
  It is a **candidate for future static parsing**; new providers should not copy
  this pattern.
- **`make` is deliberately partial.** `parse_targets()` text-parses the
  `Makefile` and, by design, does **not** resolve `include` directives or
  computed targets — it refuses to run `make -pRrq` because that would execute
  project code. This is the completeness-yields-to-safety trade-off in action;
  the same applies to `pre-commit` remote hooks, whose command lives upstream.

Note the deliberate consequence: **adoption alone never qualifies a format.**
High-popularity tools whose tasks live in imperative code or run remotely
(Gradle, Maven, GitHub Actions, Rake, `nox`, cargo-xtask) are declined despite
their reach, because enumerating their tasks would mean executing project code or
resolving remote state — a breach of the hard rule.

### Current provider landscape

Supported today: `npm`, `make`, `pdm`, `poe`, `just`, `Taskfile`, `mise`, `xc`.

Filed for addition (parse specs in the linked issues):

| Provider | Source file | Difficulty | Issue |
| ---------- | ------------- | ----------- | ------- |
| deno | `deno.json` / `deno.jsonc` | low | #71 |
| composer | `composer.json` | low | #72 |
| pre-commit | `.pre-commit-config.yaml` | medium (partial: remote hooks' command lives upstream) | #73 |
| tox | `tox.ini` / `tox.toml` / `pyproject.toml` | medium (factor/envlist expansion) | #74 |
| JS-adjacent (umbrella) | Grunt/Gulp/workspaces | decision issue | #75 |
| cargo-make | `Makefile.toml` | low | #76 |
| moon (moonrepo) | `moon.yml` | low–medium | #77 |

Deferred candidates (parseable-but-low-value, or disqualified by the criteria
above) are tracked in **#78**.

### Consequences

- Good: every provider upholds the same safety guarantee; the "should we add X?"
  question has a repeatable, objective answer.
- Good: contributors can self-assess a new format against three constraints
  before opening an issue.
- Bad / accepted: `nur` will *not* discover tasks from some of the most popular
  tools (Gradle, GitHub Actions), which may surprise users. The tracking issue
  documents why, per format.
- Follow-up: partial-support formats (e.g. pre-commit remote hooks) surface the
  task name/invocation but leave `definition` empty; that trade-off is recorded
  in the provider's issue rather than blocking the provider.

## More Information

- Selection was informed by two multi-source research passes (adoption via the
  2025 Task Runner Census, static-parseability from primary docs, and a
  competitive scan of multi-runner CLIs such as `task-keeper`).
- Adoption figures rest largely on a single source (2025 aleyan.com census) and
  should be treated as indicative, not authoritative.
- Revisit this ADR if: `nur` ever relaxes the current-directory-only scope, or a
  safe static-discovery path emerges for a currently-disqualified format.
- Template: [MADR 4.0.0](https://adr.github.io/madr/).
