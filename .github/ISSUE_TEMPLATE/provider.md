---
name: Provider Request
about: Propose a new task-discovery provider for a specific file format
title: 'Provider: add <format> provider for task discovery'
labels: enhancement
assignees: 'hasansezertasan'
---

<!--
nur discovers tasks by PARSING a file, never by executing code. A provider is
only viable if its source format can be read statically. Please work through
every section below — the static-discovery check is the deciding one.
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
nur's core guarantee: tasks are found by parsing, NEVER by running code.
A format that only reveals its tasks at runtime (imperative build scripts,
`@task` decorators, remote CI runners, arbitrary Ruby/Python/Groovy) does
NOT qualify. Be honest here — this determines whether the provider can exist.
-->

- **Can every task be enumerated by parsing the file alone (no evaluation)?** <!-- Yes / No -->
- **Are the discovered tasks locally runnable commands?** <!-- Yes / No — remote-runner or interpolated formats don't count -->
- **Must nur ever shell out to enumerate tasks (e.g. `tool -l`)?** <!-- Must be No -->

> If any answer above disqualifies the format, it belongs on the deferred /
> excluded tracking issue, not here.

## Task-model mapping

<!--
How do the format's concepts map onto nur's Task model?
-->

- `name` ← <!-- e.g. environment / target name -->
- `argv_base` ← <!-- e.g. ("tox", "-e", <name>) -->
- `description` ← <!-- where the human-readable description comes from, if any -->
- `definition` ← <!-- the underlying command(s); how multiple commands are joined -->
- `source_file` ← <!-- the file that was parsed -->

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

```
```

## Adoption

<!--
Rough usage signal (e.g. repo census count) to justify priority.
-->
