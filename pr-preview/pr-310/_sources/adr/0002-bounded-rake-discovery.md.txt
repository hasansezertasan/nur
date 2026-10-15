---
status: accepted
date: 2026-10-08
decision-makers: [hasansezertasan]
---

# Bounded Rake discovery: extract literal declarations without simulating Ruby

## Context

[ADR 0001](0001-provider-selection-criteria.md) permits useful static subsets
and requires discovery to avoid executing project code.
Rakefiles are Ruby programs, so their declarations can be read statically
without proving what happens when Ruby loads them.

The Rake provider's initial implementation grew into a partial interpreter:
exception matching, method overrides, callback execution, global-variable
constraints and control-flow reachability needed increasingly coupled checks.
Repeated reviews exposed parallel cases after each local fix.
That approach has no useful completeness boundary for arbitrary Ruby programs.

## Decision

Discover **syntactic task candidates**, with the following bounded contract:

- Read only the current directory's `Rakefile`, without Ruby, Rake, subprocesses
  or imported project files.
- Parse with Tree-sitter and skip a file if parsing reports an error.
  This is a grammar check, not Ruby compilation or semantic validation.
- Recognize direct `task` and `multitask` calls, bare or received by literal
  `self`, at file scope and inside literal namespace blocks.
- Extract a literal symbol or ordinary quoted string from the leading argument
  or a single-entry task-name dependency hash.
  Other arguments and dependency expressions are opaque.
- Traverse parentheses and plain `begin` wrappers without exception handlers.
  Namespace block parameters, handlers, dynamic namespaces, conditionals,
  loops, classes, methods, other callbacks and `BEGIN` initializers are opaque.
- Use the first line of an adjacent literal `desc` string as optional metadata.
  Comments preserve adjacency; unsupported statements and namespace boundaries
  clear it. Deduplicate names and retain the latest nonempty literal description.
- Preserve bounded source decoding and command-name checks.
  Execution delegates to `rake <qualified-name>` through an argument vector.

Discovery does **not** determine whether Ruby will compile or load the file,
whether an override changes a DSL call, whether a declaration is reached,
whether a task is registered, or whether invoking it succeeds or terminates.
Those behaviors belong to Rake when the user runs the command.

## Consequences

The provider remains useful without requiring a Ruby installation for listing.
Its implementation and review criteria have a finite structural boundary.
Names from unsupported scopes are omitted, while recognized declarations can
still be listed from a file whose loading or execution fails.
Descriptions are best-effort literal metadata, not a reproduction of `rake -T`.

Tests cover extraction, namespace qualification, metadata boundaries, parser
failures, argument construction, source decoding and absence of execution.
Runtime-semantics tests are retired rather than retained as misleading promises.
Future support expansions require an explicit update to this contract.
