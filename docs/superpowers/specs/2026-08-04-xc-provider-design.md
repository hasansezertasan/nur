# xc Provider — Design

**Date:** 2026-08-04
**Status:** Approved
**Issue:** [#36](https://github.com/hasansezertasan/nur/issues/36)

## Problem

[xc](https://xcfile.dev) defines tasks in a markdown file: a task-list heading,
task names as the headings one level below it, and each task's script as a
fenced code block. nur has no `xc` provider, so xc projects discover zero xc
tasks.

This repository is one of them. `README.md:147` holds an `<!-- xc-heading -->`
marker above `## Development :toolbox:`, with `install`, `style`, `ci`,
`docs-build`, `docs-server`, and `docs-linkcheck` beneath it. Running `nur` here
lists the `mise.toml` tasks and none of the six documented in its own README.
Every project generated from
[copier-pyproject](https://github.com/hasansezertasan/copier-pyproject) carries
the same section.

**Goal:** discover xc tasks from `README.md` by parsing markdown, never by
running `xc`.

## Decisions

- **`README.md` in the current directory only.** xc defaults to `README.md` and
  walks up parent directories to find one; nur's discovery contract is
  cwd-scoped (see the original design doc), so no upward search. `README.org`
  (org-mode) is out of scope — a second grammar for a rarer format.
- **ATX headings only.** Setext headings (`Tasks` underlined with `===`/`---`)
  are valid markdown and xc's markdown parser accepts them, but nur recognises
  only `#`-prefixed headings. The `<!-- xc-heading -->` convention and every
  documented xc example use ATX; supporting setext means look-ahead in the line
  scanner for a marginal case. Documented as a known limitation.
- **A hand-rolled, fence-aware line scanner — no markdown dependency.** The
  parser needs headings, fenced code blocks, and HTML comments. Pulling in a
  markdown library for three constructs is disproportionate for a tool whose
  runtime deps are deliberately small.

  The scanner **must** track fence state. Shell scripts contain `# comment`
  lines and `### foo`; treating one as a heading would either truncate the task
  section or invent a phantom task. Fence state is the correctness core of this
  provider, not an edge case.
- **Only the first task-list section.** xc ignores every task-list section after
  the first; nur mirrors that rather than merging, so `nur list` and `xc -s`
  agree.
- **Headings that are not valid xc task names are skipped.** xc forbids
  whitespace in task names (`-` and `_` are fine). A `### Build the docs`
  heading is prose, not a task, and there is no `xc` invocation that could run
  it. Skipped at `log.debug` — a warning per prose heading would be noise in
  READMEs that use the section for documentation.
- **Attribute lines are metadata, not description.** Lines like `requires: test`
  or `Env: CI=1` sit between the task heading and its script. They are excluded
  from `description` so the TUI detail pane shows prose, not xc bookkeeping. nur
  does not interpret them — `xc <name>` applies them at run time.
- **Inline markup around a heading's text is markup, not name.** *(Amendment,
  found during implementation by the golden fixture below.)* xc reads heading
  text from the markdown AST, so ``### `build` `` is the task `build`.
  copier-pyproject writes **every** task heading as an inline code span — this
  repository's own tasks are `` ### `install` ``, so without unwrapping, the
  provider discovers zero usable tasks in exactly the projects it targets.
  Leading/trailing backticks and asterisks are stripped from heading text; `_`
  and `~` are left alone, as both are legal inside an xc task name.
- **A task with no code block is still a task.** `requires:`-only tasks are
  valid xc (they exist to chain dependencies), so they are discovered with an
  empty `definition`.

## Architecture

A new provider module, wired into the registry exactly like the six existing
ones. No changes to `models.py`, `registry.py`, `discovery.py`, `execution.py`,
or the TUI.

```
src/nur/providers/xc.py   →  parse_xc(text, source_file) -> list[Task]
                             XcProvider (prefix = "xc")
src/nur/providers/__init__.py  →  register XcProvider in PROVIDERS
```

`parse_xc` is a module-level function taking markdown *text*, mirroring
`parse_taskfile` in `providers/task.py`, so the grammar is unit-testable without
touching the filesystem.

## Components

### Parsing grammar (`parse_xc`)

Operates on `text.splitlines()`. Two passes over one line scan:

**1. Locate the task-list section.**

While scanning, maintain fence state (see below); lines inside a fence are never
headings or markers. Track two candidates:

- **Marker candidate:** the first ATX heading that follows a line whose stripped
  content is exactly `<!-- xc-heading -->`, ignoring blank lines between the
  marker and the heading. Only blank lines may intervene — any prose or fenced
  block between the marker and the heading breaks the association.
- **`Tasks` candidate:** the first ATX heading whose text, stripped and
  lowercased, is exactly `tasks`.

The marker candidate wins if present, matching xc's documented priority.
Otherwise the `Tasks` candidate. If neither exists, return `[]`.

The section runs from the line after the heading to the first subsequent ATX
heading whose level is **≤** the section heading's level, or end of file.

**2. Collect tasks within the section.**

A task starts at an ATX heading of level exactly `section_level + 1`. Its body
runs to the next such heading or the end of the section; deeper headings
(`section_level + 2` and beyond) are body content and are not task names.

For each task heading:

- `name` — the heading text, stripped. **Skipped** if empty or containing
  whitespace.
- `definition` — the inner text of the **first** fenced code block in the body,
  fences excluded, lines joined with `\n`. `""` if the body has none.
- `description` — the body's non-blank lines that appear *before* the first
  fence and are neither attribute lines nor indented code blocks (four or more
  leading spaces, or a leading tab), whitespace-collapsed and joined with a
  single space. `None` if that leaves nothing. *(Amendment: found in review —
  indented code was leaking into the description.)*
- `prefix` — `"xc"`; `argv_base` — `("xc", name)`; `passthrough_prefix` — `()`
  (xc takes inputs as positional args: `xc greet Joe` — no `--` separator).
- `source_file` — the `source_file` argument.

**Attribute line** — a line matching, case-insensitively after stripping:

```
^(requires|req|dir|directory|env|environment|inputs|run|rundeps|interactive)\s*:
```

**Indentation bound** — a heading, fence, or HTML block may carry at most three
leading spaces; a fourth makes the line an *indented code block*. Every pattern
below is therefore anchored with `^ {0,3}`, and none of them may be matched
against a `strip()`ed line. Without this bound, a README that shows indented xc
examples advertises tasks xc cannot run. *(Amendment: found in review — the
first implementation matched stripped lines.)*

**Fence state** — an opening fence is a line of three or more backticks or
tildes, optionally followed by an info string. It closes at the next line with
the same character, repeated at least as many times, **followed by nothing but
whitespace**. The `≥` rule is what lets a ```` ```` ````-wrapped block contain
``` ``` ``` lines (as xc's own docs do); the whitespace-only rule is what keeps
a script line such as ``` ```not-a-close ``` as content. *(Amendment: found in
review — accepting a delimiter with trailing text truncated the task's script
and swallowed the following task, because the block's end shifted to the real
closing fence and re-paired every fence after it.)* An unclosed fence extends to
the end of the section.

**ATX heading** — `^ {0,3}(#{1,6})\s+(.*)$`, outside a fence. Trailing closing
hashes (`## Tasks ##`) are stripped from the text.

### `XcProvider`

```python
class XcProvider:
    prefix = "xc"

    def detect(self, cwd: Path) -> bool  # README.md exists and yields ≥1 task
    def discover(self, cwd: Path) -> list[Task]
```

`detect` returning "parses to at least one task" follows `MiseProvider`: a
`README.md` exists in nearly every repository, so file presence alone is not
evidence of an xc project. `discover` reads with `encoding="utf-8"` and, on
`OSError` or `UnicodeDecodeError`, logs `nur: skipping README.md (...)` at
warning level and returns `[]` — the same shape as the other providers. Both
methods delegate to one `_load_tasks(cwd)` helper, as `mise.py` does. Note this
reads and parses `README.md` on each call, so a discovery run that invokes both
`detect` and `discover` parses twice; that matches every existing provider and
is not memoised here.

Registered last in `PROVIDERS`, after `MiseProvider()`.

## Tests

`tests/unit/providers/test_xc.py`, driving `parse_xc` with markdown strings:

| Case | Expectation |
|---|---|
| `## Tasks` + one task with a fenced block | name, `definition`, `argv_base == ("xc", "build")` |
| `<!-- xc-heading -->` above a differently-named heading | that section is used |
| Both a marker section and a later `## Tasks` | marker wins |
| Two `## Tasks` sections | only the first is read |
| **`###` and `#` lines inside a shell code block** | not headings — no phantom tasks, section not truncated |
| `~~~` fences, and a ```` ````-wrapped block containing ``` | closed at the right line |
| A four-space-indented `## Tasks` / `### fake` example | an indented code block — no tasks at all |
| A task heading indented by one to three spaces | still a task |
| A script line of ``` ```not-a-close ``` | script content, not a closing fence |
| Task heading with spaces (`### Build the docs`) | skipped |
| Task with prose + `requires:`/`Env:`/`dir:` lines | attributes excluded from `description` |
| Task with `requires:` and no code block | discovered, `definition == ""` |
| `### Sub` heading nested inside a task body | body content, not a task |
| Section terminated by a same-level heading | later tasks not collected |
| No `README.md` / no task section | `detect()` false, `discover()` `[]` |
| Unclosed fence | terminates at section end, no crash |
| Non-UTF-8 `README.md` | warning logged, `[]` returned |

Integration: `tests/integration/test_discovery.py` — an xc `README.md` in a tmp
dir contributes `xc:`-prefixed tasks, and coexists with another provider without
ambiguity errors on distinct names.

**Golden fixture:** a test asserting that this repository's own `README.md`
parses to exactly `install`, `style`, `ci`, `docs-build`, `docs-server`,
`docs-linkcheck`. It is the real-world case, and it pins the section-boundary
behaviour (the `### TUI` / `### CLI` / `### Debugging` headings earlier in the
file must not appear, and the section must end at `## Releasing`).

## Documentation

- `README.md` — add `xc` to the intro paragraph, the Motivation dialect list,
  and the Features bullet; the bullet's "seven providers" becomes eight, with
  the `README.md`/heading-detection rule stated as mise's config-file order is.
- `docs/usage.rst` — add `xc` to the provider list at lines 4–6 and the prefix
  list at line 25. **Both are already stale**: neither mentions `mise`. Fixed in
  the same change.

## Out of scope

- `README.org` and org-mode task lists.
- xc's parent-directory search for `README.md`.
- The `-file` / `-heading` (`-H`) CLI overrides, and any nur config for them.
- Interpreting attribute semantics (shebang interpreters, `directory`, `env`,
  `inputs`, `run: once`, `rundeps`) — `xc <name>` owns execution.
- Setext headings.
