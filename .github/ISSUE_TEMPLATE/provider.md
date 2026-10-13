---
name: Provider Request
about: Propose a new task-discovery provider for a specific file format
title: 'Provider: '
labels: enhancement
assignees: 'hasansezertasan'
---

<!--
nur discovers tasks by PARSING a file, never by executing code. A format becomes
a provider when it passes three gates (ADR 0005,
docs/adr/0005-completeness-over-popularity.md): safe listing, a faithful run
command, and an honest subset. Popularity is not a gate. Please add an entry to
the provider register (docs/providers/, template: docs/providers/template.md)
before or together with this issue; the register is the source of truth.
-->

## Format

<!--
What is the format, and where does its config live?
-->

- **Format:** <!-- e.g. tox -->
- **Source file(s):** <!-- e.g. tox.ini, tox.toml, pyproject.toml [tool.tox] -->
- **Docs / spec:** <!-- link to authoritative documentation -->

## Static-discovery check (required)

<!--
Gate 1 is the SAFETY invariant: listing tasks must NEVER execute the project's
own task code or evaluate project-controlled expressions. Gate 2: a delegated
command (`<runner> <name>`) must run the task locally and faithfully. Gate 3:
enumeration may be a *safe subset* (e.g. the `make` provider text-parses and
skips `include`/computed targets; `pre-commit` skips remote hooks) but must be
honest about what it leaves opaque.
-->

- **Does listing tasks avoid executing project task code / project-controlled expressions?** <!-- Must be Yes — this is the hard rule -->
- **Can a useful subset of runnable tasks (name + run command) be parsed from the file without evaluation?** <!-- Yes / No — a safe subset is enough; full coverage is a bonus -->
- **Does discovery avoid shelling out to enumerate tasks (e.g. `tool -l`)?** <!-- Yes preferred; a parse-only dump like `just --dump` is tolerated as a temporary deviation only -->
- **Does a delegated command (`<runner> <name>`) run the task locally and faithfully?** <!-- Yes / No; say what it needs beforehand -->
- **If enumeration is partial, what is deliberately not resolved, and why is that the safety trade-off?** <!-- e.g. include directives, computed names, remote hooks -->

> If a gate fails, the format is recorded as declined in the register
> (docs/providers/declined.md) with the failed gate and a revisit trigger. If a
> gate is merely unresolved, it is recorded as pending.

## Task-model mapping

<!--
How do the format's concepts map onto nur's Task model?
-->

- `prefix` ← <!-- the provider's stable namespace, e.g. "tox" (used for qualified_name `prefix:name`) -->
- `name` ← <!-- e.g. environment / target name -->
- `argv_base` ← <!-- e.g. ("tox", "-e", <name>) -->
- `description` ← <!-- where the human-readable description comes from, if any -->
- `definition` ← <!-- the underlying command(s); how multiple commands are joined -->
- `source_file` ← <!-- the file that was parsed -->
- `passthrough_prefix` ← <!-- optional: tokens inserted before forwarded args, e.g. ("--",) for npm-style `run <script> -- <args>`; omit if not needed -->

## Parsing edge cases

<!--
The details that make a static parser correct. Consider, where relevant:
- config-file discovery / precedence order (and version differences)
- name expansion (brace/range/matrix), and whether the format expands strings
- base-section inheritance / defaults
- which sections are runnable vs. non-runnable (exclude base/provisioning)
- multi-file or multi-syntax variants of the same format
Cite primary docs / upstream issues where behavior is subtle.
-->

## Deferred (out of scope for a first cut)

<!--
Parsing complexity you intend to leave out initially, so the boundary is explicit.
-->

## Parser algorithm

<!--
Optional but encouraged: pseudocode of discovery → parse → emit Task(...).
-->

```text
```

## Adoption

<!--
Information only, with its source (or "no figure known"). Popularity is not a
reason to accept, decline or order a provider.
-->
