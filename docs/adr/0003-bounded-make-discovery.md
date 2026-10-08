---
status: accepted
date: 2026-10-08
decision-makers: [hasansezertasan]
---

# Bounded Make discovery: read rule headers with Tree-sitter, never run Make

## Context

[ADR 0001](0001-provider-selection-criteria.md) requires discovery to avoid
executing project code, which is why the `make` provider never calls
`make -pRrq`.
The previous implementation scanned single lines for target-like headers.
It could mistake text inside `define` blocks for targets and missed targets
in rule headers continued with a trailing backslash.

## Decision

Discover **syntactic task candidates** from the current directory's `Makefile`
using `tree-sitter-make`, with this contract:

- No Make, shell or subprocess runs; `$(shell ...)`, `!=` assignments, `include`
  directives and variable expansion are never evaluated or followed.
- Report literal target words from the header of each `rule` node.
  Targets made of variable references or concatenations (`$(NAME)`, `x$(Y)`),
  pattern rules (`%.o`), `.`-prefixed special targets and `Makefile` are omitted.
- `define` bodies, recipe lines, variable assignments and directives never yield
  targets. Rules inside `ifdef`/`ifeq` blocks are listed without deciding which
  branch is active.
- Multi-target, `::` and static-pattern rules yield their literal targets.
  Continued headers are supported.
- An inline `## description` on the line where the header ends supplies optional
  metadata; the first nonempty description for a name wins.
- Source order, deduplication and the `make <target>` command are unchanged.
- **Parse errors are local.** Real Makefiles often contain constructs the grammar
  rejects (shell quoting inside `$(shell ...)`, `>&`, non-ASCII names).
  Discovery logs a warning, skips only the erroneous subtrees and any rule whose
  target list is erroneous, and lists the remaining rules.
  Skipping the whole file would silently drop every task from such files.

## Consequences

Structural accuracy improves without weakening the safety rule.
Wheels for `tree-sitter-make` (MIT, `abi3`) exist for Linux (glibc and musl,
x86_64 and aarch64), macOS (x86_64 and arm64) and Windows (x64 and arm64),
matching the CI matrix.
Tasks following an unparsable region may occasionally be omitted; results remain
candidates, not a promise that Make will accept the file.
