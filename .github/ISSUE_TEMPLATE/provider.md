---
name: Provider Request
about: Propose a new task-discovery provider for a specific file format
title: 'Provider: '
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
The deciding rule (ADR 0001, docs/adr/0001-provider-selection-criteria.md) is
the SAFETY invariant, not completeness: listing tasks must NEVER execute the
project's own task code or evaluate project-controlled expressions. Enumeration
may be a *safe subset* — completeness yields to the hard rule (e.g. the `make`
provider text-parses and skips `include`/computed targets; `pre-commit` skips
remote hooks). What disqualifies a format is that discovering *any* useful
runnable task would require running project code (imperative build scripts,
`@task` decorators, arbitrary Ruby/Python/Groovy) or resolving remote state
(e.g. GitHub Actions runners).
-->

- **Does listing tasks avoid executing project task code / project-controlled expressions?** <!-- Must be Yes — this is the hard rule -->
- **Can a useful subset of runnable tasks (name + run command) be parsed from the file without evaluation?** <!-- Yes / No — a safe subset is enough; full coverage is a bonus -->
- **Does discovery avoid shelling out to enumerate tasks (e.g. `tool -l`)?** <!-- Yes preferred; a parse-only dump like `just --dump` is tolerated as a temporary deviation only -->
- **If enumeration is partial, what is deliberately not resolved, and why is that the safety trade-off?** <!-- e.g. include directives, computed names, remote hooks -->

> If the first answer is No — any useful discovery would require executing
> project code or resolving remote state — the format belongs on the deferred /
> excluded tracking issue (#78), not here.

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
Rough usage signal (e.g. repo census count) to justify priority.
-->
