# Implemented providers: evaluations

The evaluation each shipped provider was accepted on, kept in the repository so
it outlives the issue it came from. The run command shown is what `nur`
delegates to. For every provider, discovery reads files and never executes
project code or calls the runner to list.

Entries follow the [template](template.md), condensed. Where a provider has its
own ADR, that ADR is the contract and the entry only links to it.

## Providers that predate the register

`npm`, `make`, `pdm`, `poe`, `just`, `Taskfile`, `mise` and `xc` were selected
under ADR 0001 before per-format evaluations were written down. Their behavior
is defined by their provider modules and tests. Open items:

- **make:** [ADR 0003](../adr/0003-bounded-make-discovery.md). Never runs Make,
  never resolves `include` or computed targets.
- **just:** text-parsed since #91 (no `just` binary, no subprocess). A move to
  tree-sitter is open as #294, see [research](research/markdown-parser.md).
- **xc and mask:** Markdown scanners. See the
  [Markdown parser evaluation](research/markdown-parser.md) for measured
  behavior against the real runners.
- **mise:** reads `mise.toml`, `mise.local.toml`, `.mise.toml`,
  `.mise.local.toml` and `.config/mise.toml` (#32).

## deno

- **Source:** `deno.json`, then `deno.jsonc` (first existing). **Issue:** #71. **Shipped:** 0.3.0.
- **Run:** `deno task <name>`. **Never use:** `deno task` to list.
- **Reads:** top-level `tasks`; a value is a command string or an object with a
  `command`. `description` from the object form. JSONC-tolerant (comments,
  trailing commas), so strict `json.loads` is wrong.
- **Left out:** the fallback to `package.json` scripts (the `npm` provider
  already lists them, avoiding double listing); `dependencies`, `env`, `cwd`,
  `--filter` semantics. Deno does not auto-run `pre`/`post` hooks, so none are
  synthesized.
- **Adoption (information only):** about 188 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## composer

- **Source:** `composer.json`. **Issue:** #72. **Shipped:** 0.3.0.
- **Run:** `composer run-script <name>`. **Never use:** `composer run-script --list`.
- **Reads:** `scripts` (string or array; arrays joined with ` && `);
  `scripts-descriptions` supplies descriptions.
- **Left out:** PHP static-method callbacks and `@other-script` references are
  listed by name with an opaque `definition`; `@php`, `@composer`, `@putenv`,
  `@additional_args` directives are opaque. Reserved lifecycle events
  (`pre/post-install-cmd`, `pre/post-update-cmd`, `post-autoload-dump`, and the
  package, installer and plugin events) are filtered so the picker shows only
  user-defined scripts.
- **Adoption (information only):** about 3,135 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## pre-commit

- **Source:** `.pre-commit-config.yaml`. **Issue:** #73. **Shipped:** 0.5.0.
- **Run:** `pre-commit run <id> --all-files`. **Never use:** `pre-commit` to enumerate hooks.
- **Reads:** `repos[].hooks[]` only; sibling top-level keys (`ci`,
  `default_stages`, `files`, `exclude`, `fail_fast`, ...) are configuration and
  hold no hooks. Three repo kinds: **local** (`entry` is in this file, so
  `definition` is available), **remote** (`entry` lives in the upstream
  `.pre-commit-hooks.yaml`, so `definition` stays empty) and **meta** (the three
  built-ins `check-hooks-apply`, `check-useless-excludes`, `identity`).
- **Left out:** resolving remote hooks' `entry`, `language` and `args` (needs a
  clone); `stages` filtering.
- **Resolved at implementation:** plain `pre-commit run <id>` runs on staged
  files and no-ops on a clean tree, so the provider delegates with
  `--all-files`. Still unconfirmed against primary sources: the `name` versus
  `alias` rule (the provider keys on `id`).
- **Adoption (information only):** about 3,632 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## tox

- **Source:** `tox.ini`, `tox.toml`, `pyproject.toml` `[tool.tox]`, `setup.cfg`
  `[tox:tox]`. **Issue:** #74. **Shipped:** 0.5.0.
- **Run:** `tox -e <name>`. **Never use:** `tox -l` or `tox -a`.
- **Reads:** environments as the union of the expanded `env_list`/`envlist` and
  the explicit `[testenv:NAME]` (INI) or `[tool.tox.env.NAME]` (TOML) sections,
  excluding the `[testenv]` base. INI `envlist` is brace- and range-expanded
  (`{py27,py36}-django{15,16}` is a hyphen-joined Cartesian product; `py3{10-14}`
  expands too). TOML `env_list` strings are literal. Missing fields are
  inherited from `[testenv]` or `env_run_base`.
- **Config discovery is version dependent.** tox 3 uses `pyproject.toml`,
  `tox.ini`, `setup.cfg`; tox 4 adds native `pyproject.toml` and `tox.toml`, and
  its real precedence was buggy until tox PR #3742 (merged Feb 2026, issue
  #3645). Pick the first existing file by a documented order and do not
  over-trust cross-version precedence.
- **Also read:** generative section headers `[testenv:{a,b}]`, factor-conditional
  commands (filtered by the current environment's factors) and `labels` (INI and
  TOML).
- **Left out:** TOML structured matrices (`range`, `labeled`, `product`) and
  provisioning environments (`.pkg`, `.tox`), treated as non-runnable.
- **Adoption (information only):** about 1,869 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## cargo-make

- **Source:** `Makefile.toml`. **Issue:** #76. **Shipped:** 0.3.0.
- **Run:** `cargo make <name>`. **Never use:** `cargo make --list-all-steps`.
- **Reads:** `[tasks.<name>]` tables; `description`; `command` plus `args`, or
  `script` (a list joined with ` && `) as `definition`.
- **Left out:** `extends`, `env`, `dependencies`, `condition`, profile-specific
  `env`, cargo-make's built-in predefined tasks (only locally declared tasks
  appear), and the `makers` alias.

## moon

- **Source:** `moon.yml` in the current directory. **Issue:** #77. **Shipped:** 0.4.0.
- **Run:** `moon run <name>`. **Never use:** `moon query tasks`.
- **Reads:** top-level `tasks:`; `description`; `command` plus `args`, or `script`.
- **Left out:** inherited tasks from `.moon/tasks.yml` and `.moon/tasks/**`,
  `.moon/workspace.yml` (project locations, not tasks), project-qualified
  `<project>:<task>` addressing, and `deps`, `inputs`, `outputs`, `options`.

## VS Code tasks

- **Source:** `.vscode/tasks.json`. **Issue:** #139. **Spec:**
  <https://code.visualstudio.com/docs/editor/tasks>.
- **Run:** the task's own `command` and `args`.
- **Reads:** schema `version` `"2.0.0"`, JSONC. Task `label` is the name,
  `detail` the description. `shell`, `process` and untyped tasks with a
  `command` are runnable. A task or the document may carry `windows`, `linux` and
  `osx` override objects, merged for the current platform; `command` and `args`
  items may be strings or `{ "value", "quoting" }`.
- **Left out:** the legacy `"0.1.0"` schema; extension-contributed task types
  (`npm`, `typescript`, `gulp`, `grunt`, `cargo`, ...), which the extension
  resolves at runtime; any task with a non-empty `dependsOn` (even one that also has its own
  `command`); editor-only variables
  (`${file}`, `${selectedText}`, `${input:...}`): a task whose command, args or
  working directory still contains an unresolved variable is **skipped**, not
  listed (`${workspaceFolder}` resolves to the discovery directory). Duplicate
  labels are tolerated.
- **Sibling:** Zed `.zed/tasks.json`, see [candidates](candidates.md).

## invoke

- **Source:** `tasks.py`. **Issue:** #140. **Docs:** <https://docs.pyinvoke.org/en/stable/>.
- **Run:** `invoke <name>`. **Never use:** `inv -l` (imports and executes the module).
- **Reads:** AST of `@task`-decorated functions, including `@task(...)`,
  `@invoke.task` and aliased imports; explicit `name=` and `aliases=`; first
  docstring line as `description`. The module is never imported.
- **Left out:** command bodies (`c.run(...)`), parameters, `pre`/`post` graphs,
  `Collection` namespacing (so namespaced setups are under-covered), and tasks
  built dynamically or imported from other modules.
- **History:** first parked "low priority" on reliability and value; accepted
  once value stopped being a gate (ADR 0004 and 0005).

## nox

- **Source:** `noxfile.py`. **Issue:** #142. **Docs:** <https://nox.thea.codes/en/stable/>.
- **Run:** `nox -s <name>`. **Never use:** `nox -l`.
- **Reads:** AST of `@nox.session`, `@nox.session(...)` and `@session` (aliased
  import); explicit `name=`; docstring first line.
- **Left out:** `python=[...]` and `@nox.parametrize` expansion (concrete names
  such as `tests-3.11` are runtime-generated, so only the base session name is
  listed, and `nox -s tests` still runs all its variants), dynamically registered
  sessions, command bodies.

## Apache Ant

- **Source:** `build.xml`. **Issue:** #141. **Docs:** <https://ant.apache.org/manual/>.
- **Run:** `ant <target>`. **Never use:** `ant -p`.
- **Reads:** `<target name=...>` and `description`. `depends` ordering and
  `<project default>` are not read (an evaluation idea, not shipped). Declarative XML, so the safest of the imperative-adjacent set.
  Undescribed targets are conventionally internal (Ant hides them from `-p`).
- **Left out:** targets from `<import>` and `<include>`, `<macrodef>` and
  `<presetdef>` output, `${property}` interpolation (kept literal), non-default
  file names (`ant -f foo.xml`). `if`/`unless` targets are still named, so still
  listed.

## Rake

- **Contract:** [ADR 0002](../adr/0002-bounded-rake-discovery.md). **Issue:** #143.
- **Run:** `rake <qualified-name>`. **Never use:** `rake -T`.
- **Notes from the evaluation:** namespacing determines the runnable name
  (`rake db:migrate`, not `rake migrate`), so nested `namespace` blocks must be
  tracked; `desc` binds to the next `task`. First implementation grew into a
  partial Ruby interpreter and was reset to the bounded syntactic contract.

## mask

- **Source:** `maskfile.md`. **Issue:** #144. **Docs:** <https://github.com/jacobdeichert/mask>.
- **Run:** `mask <path...>` (for example `mask test lint`).
- **Reads:** the Markdown heading tree as the command tree; the `>` blockquote as
  description; the fenced body as `definition`; heading args `(pattern)` stripped
  from the name. A top-level title or prose heading with no code block or
  subcommands is not a task.
- **Overlap:** it shares machinery with `xc`; that was the reason it was first
  parked, and it no longer is one.
- **Measured behavior:** [Markdown parser evaluation](research/markdown-parser.md).

## Grunt, Gulp, Turborepo, Nx, workspaces, npm-run-all

[ADR 0004](../adr/0004-js-adjacent-task-runners.md) holds the full evaluation
table, per-provider read boundaries and consequences. Summary:

- **Grunt:** literal `grunt.registerTask` and `registerMultiTask` names via
  tree-sitter; run `grunt <name>`.
- **Gulp:** literal `gulp.task(name, ...)` and CommonJS `exports.name`; run
  `gulp <name>`.
- **Turborepo:** `turbo.json` `tasks` keys (legacy `pipeline`); run
  `turbo run <task>`.
- **Nx:** `nx.json` `targetDefaults` keys; run `nx run-many -t <name>`.
- **workspace:** members from `pnpm-workspace.yaml`, `package.json`
  `workspaces`, `lerna.json`; tasks named `<package>:<script>`, run through the
  lockfile-resolved package manager. Rooted traversal, never escaping `cwd`.
- **npm-run-all, run-p, run-s:** no provider; they compose `package.json`
  scripts the `npm` provider already lists.
