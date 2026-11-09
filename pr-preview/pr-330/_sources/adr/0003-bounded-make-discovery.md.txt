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
  Metadata can follow a plain `#` comment; `##` inside an inline recipe is omitted.
- Source order, deduplication and the `make <target>` command are unchanged.
- `MakeProvider.discover(cwd)` is the supported entry point for discovery.
  The previously exported `parse_targets(text)` and `parse_descriptions(text)`
  helpers are removed: callers must read names and descriptions from the returned
  `Task` objects. This is a breaking change for imports of those helpers.
  It supersedes ADR 0001's historical reference to `parse_targets()`.
- **Parse errors are local, and the line scan is the safety net.**
  Real Makefiles often contain constructs the grammar rejects or parses incorrectly
  (parentheses in comments, shell quoting inside `$(shell ...)`, `>&`,
  target-specific variables, non-ASCII names), and the parser can fuse such text
  with the next rule.
  Discovery logs a warning and re-reads only the affected statements with the
  previous line-based scan: error nodes, target-specific variable lines, rules
  whose header is fused with preceding text and rules not starting a line.
  Clean rules still come from tree-sitter nodes.
  Fallback joins continued lines before scanning, so assignment values stay opaque
  and continued target-specific variable headers retain their literal targets.
  Skipping the whole file, or only the erroneous nodes, would silently drop
  tasks that the previous implementation listed.

## Consequences

Structural accuracy improves without weakening the safety rule.
Wheels for `tree-sitter-make` (MIT, `abi3`) exist for glibc Linux (x86_64 and
aarch64), musl Linux (x86_64 only), macOS (x86_64 and arm64) and Windows
(x64 and arm64), which covers the CI matrix. Other platforms build from the
sdist and need a C compiler.
Known grammar limits remain: nested `define` blocks leak their inner text, and
targets named like directive keywords (`export:`, `include:`) can be misread.
Fallback regions skip `define` bodies, including nested and unterminated blocks.
Results remain syntactic task candidates, not a promise that Make will accept the file.
