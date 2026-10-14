---
status: accepted
date: 2026-10-14
decision-makers: [hasansezertasan]
---

# Accept Gradle and Zed tasks, and delegate Procfile to honcho

## Context and Problem Statement

[ADR 0005](0005-completeness-over-popularity.md) re-triaged the open candidates.
Three of its rows left a judgment open or made one the maintainer has since
reversed:

- **Procfile** was *pending* on gate 2: no delegated runner had been chosen.
- **Gradle** was *declined* on gate 3, on the view that a labelled subset of
  literal task declarations misses nearly every task people run (`build`, `test`
  and other plugin-contributed tasks) and so is not honest even when labelled.
- **Zed tasks** were *pending evaluation*.

ADR 0005 says a decline is "a recorded fact about the format, not a priority
call". Gate 3 as written there accepts "a safe subset" that documents what it
leaves opaque and presents results as syntactic candidates. The Gradle decline
applied a stricter reading than the gate states. This record settles all three.

## Decision Outcome

This record updates the re-triage table of ADR 0005; the rest of that record
stands.

1. **Gradle: accepted, as a labelled subset.** The provider reads literal task
   declarations from the root build script (`build.gradle`, else
   `build.gradle.kts`, matching Gradle's own precedence when both exist) and
   lists them as syntactic candidates. It never runs Gradle, the wrapper or the
   Tooling API to enumerate (`gradle tasks` stays forbidden by gate 1). Tasks
   contributed by plugins and conventions, dynamic names and subproject wiring
   stay opaque, and the provider's documentation and listing text say so. The
   decline reasoning is kept in the [register](../providers/candidates.md) as
   history. Maven stays declined.
2. **Procfile: accepted; delegates to honcho.** The delegated command is
   `honcho start <name>`. Validated against honcho 2.0.0: it runs exactly the
   named process type, passes the process's exit code through (`rc=3` for a
   failing process), and rejects an unknown type with an error. Honcho reads
   every argument after the name as another process type, so the provider must
   not forward extra arguments.
3. **Zed tasks: accepted.** Evaluated against the three gates in the
   [register](../providers/candidates.md). It follows the VS Code provider
   contract: the task's own `command` and `args` are the delegated run, since
   Zed has no command-line way to run a task by label.

Implementation of each is tracked in its own issue; the register entries carry
the specification.

### Why honcho for Procfile

| Manager | Runs one process type | Needs | Verdict |
| ------- | --------------------- | ----- | ------- |
| honcho | `honcho start <name>` | Python (`uvx honcho`, `pip`) | **Chosen:** installs without a Ruby, Go or tmux toolchain, matching how `nur` itself is installed |
| foreman | `foreman start <name>` | Ruby gem | Reference implementation; same invocation, heavier prerequisite |
| overmind | `overmind start -l <name>` | Go binary and tmux (Linux, BSD, macOS only) | tmux required; no Windows |
| hivemind | not documented in its README | Go binary | Not validated; no documented single-process selection |

The manager is a fixed default, not auto-detected: detection would make the
same `Procfile` run differently on different machines (gate 2). Overrides and
detection are a later, separate decision.

## Consequences

- Good: three open register entries become scheduled work; no format waits on
  an undecided question.
- Good: the Gradle subset gives agents and developers the project's own custom
  tasks, with the boundary stated, instead of nothing.
- Bad / accepted: the Gradle listing omits most tasks people actually run, and a
  reader may assume it is complete. The mitigation is the required wording in
  the provider docs and listing; the documented revisit trigger for a fuller
  provider is a machine-readable task manifest.
- Bad / accepted: honcho must be installed for Procfile tasks to run; discovery
  does not depend on it.

## More Information

- Builds on [ADR 0005](0005-completeness-over-popularity.md), which this record
  amends for Procfile, Gradle and Zed tasks.
- Register entries: [candidates](../providers/candidates.md).
- Revisit if: honcho becomes unmaintained or stops running single process types.
