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
  arbitrary project code. Discovery reads files; only the user's explicit choice
  runs anything.
- **Reliability** — a provider must enumerate tasks completely and correctly from
  the file alone, without a tool subprocess or network access.
- **Scope** — discovery is limited to the current directory; providers parse a
  single well-known source file.
- **Value** — real-world adoption and distinct ecosystem coverage, weighed
  *after* the hard constraints above, not before.

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

1. Tasks are discoverable by **statically parsing a single source file** in the
   current directory.
2. Discovery requires **no code execution and no subprocess/network** — the tool
   itself is never invoked to list tasks.
3. The file yields at least a task **name** and a **run command** (a description
   is a bonus).

Formats that clear the bar are then prioritized by adoption and by covering an
ecosystem `nur` does not yet reach. Markdown/prose formats are allowed (we already
ship `xc`), so option 3 is rejected as too narrow. Option 1 is rejected outright:
shelling out to enumerate tasks breaks driver #1 (safety) and #2 (reliability).

Note the deliberate consequence: **adoption alone never qualifies a format.**
High-popularity tools whose tasks live in imperative code or run remotely
(Gradle, Maven, GitHub Actions, Rake, `nox`, cargo-xtask) are declined despite
their reach, because they cannot satisfy constraints 1–2.

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
