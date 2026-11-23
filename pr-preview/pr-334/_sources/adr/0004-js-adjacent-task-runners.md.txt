---
status: accepted
date: 2026-10-11
decision-makers: [hasansezertasan]
---

# JS-adjacent runners: accept Grunt, Gulp, Turborepo, Nx, and workspace members

## Context and Problem Statement

The `npm` provider already covers `package.json` scripts, but the JavaScript
ecosystem has more task-running machinery around it: Grunt, Gulp, script
composers (`npm-run-all` / `run-p` / `run-s`), and workspace orchestrators
(pnpm / yarn / bun workspaces, Lerna, Turborepo, Nx). Issue #75 scoped this as
a **decision + phased breakout**: confirm which candidates are worth their own
provider under [ADR 0001](0001-provider-selection-criteria.md), then split each
accepted candidate into its own implementation issue.

This record was first written with conservative verdicts (decline most,
park Grunt/Gulp). On review, the maintainer reversed that direction: adoption
is retired as a gate — `nur` should enlighten the terminal for as many people
and agents as possible, so every candidate that can be supported honestly is
**accepted**, and all five providers are implemented in the same change rather
than phased. This revision records that decision and its consequences,
including the one scope change it requires.

The original #75 sketch assumed Grunt/Gulp "fail" the declarative-parse test
and are regex-scrape only. That was imprecise for the same reason issue #78
notes: the ADR 0001 hard rule is that *listing* must not execute project code,
and task *names* are often statically readable even when the command body is
imperative, with execution delegating to the runner (`grunt <name>`,
`gulp <name>` — the same shape as the accepted `invoke`, `nox`, and `rake`
providers).

## Decision Drivers

- **Safety (hard rule, unchanged)** — discovery parses files; it never runs
  the project's task code or shells out to enumerate (`grunt --help`,
  `gulp --tasks`, `turbo run`, `nx show` are all forbidden for listing).
- **Coverage over adoption (changed)** — ADR 0001 ranked qualifying formats by
  adoption × ecosystem fit. That gate is retired: a format that clears safety
  and yields a faithful run command is accepted regardless of popularity.
  This record partially supersedes ADR 0001 on that clause.
- **Rooted scope (changed)** — ADR 0001 limited discovery to "a single
  well-known source file in the current directory". Workspace runners force
  the question: member `package.json` scripts are real tasks `nur` would
  otherwise miss. Scope is relaxed to **rooted at the current directory**:
  providers may read files under `cwd` reached through workspace globs, still
  by static parsing only, never escaping `cwd`. This record partially
  supersedes ADR 0001 on that clause. Everything else in ADR 0001 stands.
- **Honesty over completeness** — where only a safe subset is enumerable
  (imperative bodies, plugin-loaded tasks, per-project files), list the
  subset as syntactic candidates and say so, following the rake/make
  precedent.

## Considered Options

| Candidate | Source file(s) | Static basis (verified against primary docs) | Verdict |
| --------- | -------------- | -------------------------------------------- | ------- |
| Grunt | `Gruntfile.js` | Literal `grunt.registerTask(name, ...)` / `grunt.registerMultiTask(name, ...)` names (plus `grunt.task.*` aliases) read via Tree-sitter without running JS; run delegates to `grunt <name>`. Plugin-loaded tasks (`loadNpmTasks`) and alias bodies stay opaque. | Accepted (new `grunt` provider) |
| Gulp | `gulpfile.js` (+ `Gulpfile.js`, `.mjs`/`.cjs` variants) | Literal `gulp.task(name, ...)` names and CommonJS `exports.name` assignments read via Tree-sitter without running JS; run delegates to `gulp <name>`. ESM `export` forms beyond plain function/const exports and `series()`/`parallel()` composition stay opaque. | Accepted (new `gulp` provider) |
| npm-run-all / run-p / run-s | none | No config file of its own; composes existing `package.json` scripts — every runnable unit is already surfaced by the `npm` provider. | No provider (already covered, not declined) |
| pnpm / yarn / bun workspaces, Lerna | `pnpm-workspace.yaml`, `package.json` (`workspaces`), `lerna.json` + member `package.json` files | Globs locate members; member `scripts` are real tasks. Run delegates per package manager (`pnpm --filter`, `yarn workspace`, `npm --workspace`, `bun --filter`, `lerna run --scope`). | Accepted (new `workspace` provider, rooted traversal) |
| Turborepo | `turbo.json` | Declarative JSON: each key of `tasks` (v2; legacy `pipeline` in v1) is runnable via `turbo run <task>`, resolved against member `package.json` scripts. Root tasks list under their stripped name, keeping `//#` identity on collision. | Accepted (new `turbo` provider) |
| Nx | `nx.json` | Root `targetDefaults` literal keys name the shared tasks (executor keys and globs skipped, array-form defaults accepted); run delegates to `nx run-many -t <name>` across projects. Per-project files stay opaque. | Accepted (new `nx` provider) |

## Decision Outcome

- **Five new providers land in this change:** `grunt`, `gulp`, `turbo`, `nx`,
  `workspace`. Phasing is dropped: the breakout issues #75 envisioned are
  replaced by the implementation itself.
- **Grunt/Gulp parse imperatively-written files safely.** Tree-sitter
  (`tree-sitter-javascript`, same pattern as the `rake` provider) extracts
  literal task names; descriptions come from adjacent literals where the API
  offers them (Grunt's optional description argument, Gulp's `.description`
  assignments). Bodies, plugin tasks, and composed targets stay opaque, and
  the providers say so in their docstrings and README notes.
- **`workspace` traverses member packages.** Globs come from
  `pnpm-workspace.yaml` (`packages`), `package.json` (`workspaces`), and
  `lerna.json` (`packages`, or `package.json` via `useWorkspaces`); only
  directories under `cwd` containing a `package.json` with scripts become
  members. Tasks are named `<package>:<script>` under the `workspace` prefix
  (bare names resolve when unambiguous, per the registry's existing rule) and
  run through the lockfile-resolved package manager. Negations and
  `node_modules` are skipped.
- **`turbo` reads root `tasks` keys** (falling back to legacy `pipeline`),
  including `//#<name>` root tasks under their stripped name; `definition` stays
  empty (orchestration config, not the command), matching the
  pre-commit/rake precedent for opaque bodies.
- **`nx` reads root `targetDefaults` keys** as the shared task list with
  `nx run-many -t <name>` delegation — syntactic candidates, not a promise
  every project implements each target.
- **npm-run-all needs nothing.** Its tasks are `package.json` scripts by
  definition; the `npm` provider already lists them.

## Consequences

- Good: monorepo, Grunt, Gulp, Turborepo, and Nx users all get discovery with
  the same never-executes guarantee; no candidate is left parked on adoption.
- Good: the value clause of ADR 0001 is simplified — contributors no longer
  argue popularity, only safety and a faithful run command.
- Bad / accepted: a new runtime dependency (`tree-sitter-javascript`) and a
  wider discovery surface (member files) to maintain; Grunt/Gulp subsets miss
  plugin/composed tasks, and Nx misses per-project-only targets. Each
  provider documents its boundary.
- Bad / accepted: rooted traversal reads more files than single-file
  discovery; very large monorepos list more tasks. Confinement to `cwd` and
  JSON-only parsing bound the risk.
- Follow-up: index the new providers from #78 alongside the existing
  candidate/declined entries.

## More Information

- Umbrella issue #75; provider criteria ADR 0001; candidate/declined index #78.
- Primary sources consulted: `gruntjs.com` (creating tasks, `grunt.task` API,
  sample Gruntfile), `gulpjs.com` (creating tasks, `task` API, gulpfile guide),
  `turborepo.dev` (configuring `turbo.json`, crafting tasks, package
  configurations).
- Template: [MADR 4.0.0](https://adr.github.io/madr/).
