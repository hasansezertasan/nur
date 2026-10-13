# Implemented providers: evaluations

The evaluation each shipped provider was accepted on, kept in the repository so
it outlives the issue it came from. These entries record the **evaluation at
acceptance**. The provider module docstring and its tests are authoritative for
current behavior, which can have grown since (for example VS Code inheritance and
Ant extension points); when they disagree, fix the entry. The run command shown
is what `nur` delegates to. For every provider, discovery reads files and never executes
project code or calls the runner to list.

Entries follow the [template](template.md), condensed. Where a provider has its
own ADR, that ADR is the contract and the entry only links to it.

## Providers that predate the register

`npm`, `make`, `pdm`, `poe`, `just`, `Taskfile`, `mise` and `xc` were selected
under ADR 0001 before per-format evaluations were written down. Their entries
below were backfilled from the provider modules and tests, not from issue text,
and are checked against the three gates of
[ADR 0005](../adr/0005-completeness-over-popularity.md). Adoption: no figure for
these eight is recorded in the repository, so each entry says so rather than
quoting one. Observed gaps are recorded as they are, not fixed here.

## npm

- **Format:** `scripts` of a Node package. **Source:** `package.json` (strict
  JSON). **Docs:** <https://docs.npmjs.com/cli/using-npm/scripts>.
- **Run:** `<pm> run <name>`, where the package manager comes from the lockfile
  (first match of `bun.lock`, `bun.lockb`, `pnpm-lock.yaml`, `yarn.lock`,
  `package-lock.json`; `npm` when none exists). Extra arguments follow `--`.
  **Never use:** `npm run` with no argument to list.
- **Gate check:** (1) safe: the file is parsed as JSON and no runner is called.
  (2) faithful: the script runs under the package manager the lockfile names.
  (3) honest subset: names and command strings are read; nothing is evaluated.
- **Mapping:** `prefix` is always `npm`, even when the package manager is pnpm,
  yarn or bun; `name` is the script key; `argv_base` is `(pm, "run", name)`;
  `definition` is the script string (`str()` of the value); no `description`;
  `source_file` is `package.json`; `passthrough_prefix` is `("--",)`.
- **Edge cases:** a missing, null or empty `scripts` lists nothing; a `scripts`
  that is not an object, malformed JSON or an unreadable file logs a warning and
  lists nothing. A JSON document whose top level is not an object, and a file
  that is not valid UTF-8, are not guarded.
- **Opaque:** `pre*`/`post*` hooks are listed as ordinary scripts and not run
  specially by `nur`; `packageManager`, `engines` and workspaces (see
  `workspace` in ADR 0004) are not read.
- **Adoption (information only):** no figure known in the repository.

## make

- **Format:** Make rules. **Source:** `Makefile` in the current directory only
  (not `makefile` or `GNUmakefile`). **Contract:**
  [ADR 0003](../adr/0003-bounded-make-discovery.md). **Docs:**
  <https://www.gnu.org/software/make/manual/>.
- **Run:** `make <target>`. **Never use:** `make -pRrq` or any invocation that
  makes Make read the file: it evaluates `$(shell ...)` and `!=` while parsing.
- **Gate check:** (1) safe: tree-sitter-make syntax tree plus a line fallback;
  tests prove `$(shell ...)` and `!=` never run. (2) faithful: `make <target>`
  is what the file declares. (3) honest subset: syntactic candidates only, as
  ADR 0003 states.
- **Mapping:** `prefix` `make`; `name` the target; `argv_base` `("make", name)`;
  `description` from a `##` comment on the rule's own header line (first
  non-empty one wins; a multi-target rule gives each target the text); no
  `definition`; `source_file` `Makefile`.
- **Edge cases:** names match `[A-Za-z0-9][A-Za-z0-9._/-]*`. Kept: multi-target,
  `::` and static-pattern rules, backslash-continued headers, rules inside
  conditionals (no branch is evaluated), target-specific variable targets.
  Skipped: `.`-prefixed specials such as `.PHONY`, `%` pattern rules, computed
  names (`$(X)`), assignments, recipe lines, `define` bodies, `include`
  directives (never followed). Non-ASCII name fragments are rejected, not
  truncated. A BOM is stripped and line endings normalised. Input the grammar
  rejects logs a warning and goes through the line fallback, so valid rules
  around the damage are still listed. An unreadable `Makefile` lists nothing.
- **Opaque:** variable expansion, included files, the active branch of a
  conditional, prerequisites, recipe bodies and the default goal. Known limits
  (nested `define`, targets named like directive keywords) are in ADR 0003.
- **Adoption (information only):** no figure known in the repository.

## pdm

- **Format:** PDM user scripts. **Source:** `[tool.pdm.scripts]` in
  `pyproject.toml`. **Docs:**
  <https://pdm-project.org/latest/usage/scripts/>.
- **Run:** `pdm run <name>`. **Never use:** `pdm run --list`.
- **Gate check:** (1) safe: `tomllib` only; `pdm` is never called. (2) faithful:
  `pdm run <name>` is the documented way to run a script. (3) honest subset:
  names, a description and a flat command are read; composite behaviour is not.
- **Mapping:** `prefix` `pdm`; `argv_base` `("pdm", "run", name)`; `description`
  from `help` when it is a string; `definition` from a string value, or a table's
  `cmd`, else `shell`, else `call` (the first present wins; a list body is joined
  with spaces); `source_file` `pyproject.toml`; no `passthrough_prefix`.
- **Edge cases:** detection needs a non-empty scripts table. The reserved `_`
  table (shared options) is skipped. A bare list value (a composite) is listed,
  with `definition` the Python `repr` of the list, so treat it as opaque. A table
  with only `composite` has an empty definition. Malformed TOML logs a warning
  and lists nothing. The `call` form is handled but has no test.
- **Opaque:** `composite` expansion, `env`, `env_file`, `working_dir`,
  `site_packages`, `{args}` placeholders and `pre_`/`post_` hooks.
- **Adoption (information only):** no figure known in the repository.

## poe

- **Format:** Poe the Poet tasks. **Source:** `[tool.poe.tasks]` in
  `pyproject.toml` (no `poe_tasks.toml`). **Docs:**
  <https://poethepoet.natn.io/>.
- **Run:** `poe <name>`, the bare binary (not `poe run`, not `uv run poe`).
  **Never use:** `poe` with no arguments, which prints the task list.
- **Gate check:** (1) safe: `tomllib` only. (2) faithful: `poe <name>` runs the
  declared task. (3) honest subset: as for pdm.
- **Mapping:** `prefix` `poe`; `argv_base` `("poe", name)`; `description` from
  `help`; `definition` from a string, or a table's `cmd`, else `shell`, else
  `script`, a list body joined with spaces; `source_file` `pyproject.toml`; no
  `passthrough_prefix`.
- **Edge cases:** detection needs a non-empty tasks table. A sequence task (a
  TOML array) is listed with the `repr` of the list as `definition`. A table with
  only `sequence`, `switch`, `expr` or `ref` has an empty definition. Names
  starting with `_` are listed; the tests do not pin that. Malformed TOML logs a
  warning and lists nothing. A project that also has `[tool.pdm]` does not leak
  pdm scripts into poe (tested). The `shell` and `script` forms are handled but
  untested.
- **Opaque:** `args`, `env`, `cwd`, `[tool.poe]` options such as `executor`.
- **Adoption (information only):** no figure known in the repository.

## just

- **Format:** just recipes. **Source:** `justfile`, else `Justfile` (not
  `.justfile`). **Docs:** <https://just.systems/man/en/>. **Issue:** #91.
- **Run:** `just <name>`. **Never use:** `just --dump` or `just --list`. Before
  #91 the provider shelled out to `just --dump`; it now needs no `just` binary.
- **Gate check:** (1) safe: a text scan, no subprocess. (2) faithful: `just
  <name>`. (3) honest subset: recipe names and doc text only.
- **Mapping:** `prefix` `just`; `argv_base` `("just", name)`; a leading `@` is
  dropped from the name; `description` from a `[doc("...")]` attribute, else the
  `#` comment directly above; no `definition`; `source_file` the file found; no
  `passthrough_prefix`.
- **Edge cases:** a header is an unindented `name args: deps` line whose `:` is
  not followed by `=`, which excludes `alias`, `export X :=` and `set`. Parameters
  and dependencies are skipped. Indented lines are bodies. A blank line or
  non-comment line clears a pending comment; attribute lines between comment and
  recipe keep it. `doc(...)` inside another attribute's string is not mistaken
  for a description. Triple-quoted and triple-backtick literals are skipped so
  unindented text inside them is not read as a recipe (the toggle is naive about
  quote escaping). The first of a duplicate name wins. `[private]` and
  `_`-prefixed recipes are listed, although `just --list` hides them. An
  unreadable or non-UTF-8 file logs a warning and lists nothing.
- **Opaque:** `alias`, `import`, `mod`, parameters, dependencies, settings,
  `[group]` and `[private]` semantics. A move to tree-sitter is open as #294, see
  [research](research/markdown-parser.md).
- **Adoption (information only):** no figure known in the repository.

## Taskfile

- **Format:** go-task. **Source:** `Taskfile.yml`, else `Taskfile.yaml` (other
  spellings and `includes:` are not read). **Docs:** <https://taskfile.dev/>.
- **Run:** `task <name>`. **Never use:** `task --list`: go-task evaluates dynamic
  `sh:` variables while loading, which can run repository-controlled commands. A
  test plants a `vars.<X>.sh` sentinel and proves it never runs.
- **Gate check:** (1) safe: `yaml.safe_load` of the top-level `tasks` mapping.
  (2) faithful: `task <name>`. (3) honest subset: names and `desc` only.
- **Mapping:** `prefix` `task`; `argv_base` `("task", name)`; `description` from
  `desc` when it is a string (an empty string stays empty); no `definition`;
  `source_file` the file found; no `passthrough_prefix`.
- **Edge cases:** a shorthand string or list task is listed with no description.
  `internal: true` tasks are skipped (the literal boolean only). A root or
  `tasks` that is not a mapping lists nothing. Names are listed as written,
  including `:` namespaces and `_` or `.` prefixes. Malformed YAML and read
  errors log a warning and list nothing. A file that is not valid UTF-8 is not
  caught.
- **Opaque:** `includes:` and the namespaced tasks they bring, `aliases`,
  `summary`, `deps`, `cmds`, `vars`, `dir`, `platforms`.
- **Adoption (information only):** no figure known in the repository.

## mise

- **Format:** mise tasks. **Source:** the first existing of `mise.local.toml`,
  `mise.toml`, `.mise.local.toml`, `.mise.toml`, `.config/mise.toml`. **Docs:**
  <https://mise.jdx.dev/tasks/>. **Issue:** #32.
- **Run:** `mise run <name>`. **Never use:** `mise tasks ls`.
- **Gate check:** (1) safe: `tomllib` only. (2) faithful: `mise run <name>`.
  (3) honest subset: one file's `[tasks]` table.
- **Mapping:** `prefix` `mise`; `argv_base` `("mise", "run", name)`;
  `description` from a table's `description`; `definition` from `run` (a list
  joined with ` && `); `source_file` the file chosen; no `passthrough_prefix`.
- **Edge cases:** only the first file found is read and its siblings are
  ignored, so mise's own merge of local and shared config is **not** replicated
  (a `mise.local.toml` hides `mise.toml`). Detection needs a non-empty `[tasks]`.
  String and list shorthands work. `hide = true` tasks are skipped, matching
  `mise tasks ls`. A table without `run` (for instance a `file` task) has an empty
  definition. Colon names such as `"build:fast"` pass through; nested tables are
  not flattened. Malformed or non-UTF-8 TOML logs a warning and lists nothing,
  without falling back to another file.
- **Opaque:** file tasks (`mise_tasks/`, `.mise/tasks/`), `alias`, `depends`,
  `dir`, `env`, `sources`, `outputs`, `tools`, parent and global config.
- **Adoption (information only):** no figure known in the repository.

## xc

- **Format:** xc, tasks written as Markdown headings. **Source:** `README.md` in
  the current directory only. **Docs:** <https://xcfile.dev/task-syntax/task-list/>.
  **Issue:** #36.
- **Run:** `xc <name>`. **Never use:** `xc -s`, because xc executes task scripts.
- **Gate check:** (1) safe: a Markdown scan; xc is never called. (2) faithful:
  `xc <name>`. (3) honest subset: names, description and first script only.
- **Mapping:** `prefix` `xc`; `argv_base` `("xc", name)`; `description` the prose
  between the heading and the first fence, attribute lines removed; `definition`
  the first fenced block's contents; `source_file` `README.md`; no
  `passthrough_prefix` (extra arguments are appended positionally).
- **Edge cases:** the task section is the heading preceded by `<!-- xc-heading -->`
  (which wins), else the first heading named `Tasks` (case-insensitive, emphasis
  stripped). It ends at the next heading of the same or higher level. Tasks are
  headings exactly one level deeper; deeper headings are body. Headings inside
  fences, indented code (4+ spaces) and bare `##` lines are not headings. A name
  that is empty or has whitespace is skipped. Tilde, nested and unclosed fences
  are handled. A task with no script is still listed. Detection needs at least one
  task. Non-UTF-8 or unreadable files log a warning and list nothing.
- **Opaque:** attribute values (`requires`, `dir`, `env`, `inputs`, `run`,
  `rundeps`, `interactive`), script language, further fences, files other than
  `README.md`. Measured behavior against the real runner:
  [Markdown parser evaluation](research/markdown-parser.md).
- **Adoption (information only):** no figure known in the repository.

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
  `.pre-commit-hooks.yaml`, so `definition` stays empty). `meta` repos (the
  built-ins `check-hooks-apply`, `check-useless-excludes`, `identity`) are not
  special-cased: they are listed like remote hooks. Hooks without an `id` are
  skipped.
- **Left out:** resolving remote hooks' `entry`, `language` and `args` (needs a
  clone); `stages` filtering.
- **Resolved at implementation:** plain `pre-commit run <id>` runs on staged
  files and no-ops on a clean tree, so the provider delegates with
  `--all-files`. Still unconfirmed against primary sources: the `name` versus
  `alias` rule (the provider keys on `id`).
- **Adoption (information only):** about 3,632 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## tox

- **Source:** `tox.ini`, `setup.cfg` (`[tox:tox]` section), `pyproject.toml`
  (`[tool.tox]`, including `legacy_tox_ini`), `tox.toml`, in that order. **Issue:** #74. **Shipped:** 0.5.0.
- **Run:** `tox -e <name>`. **Never use:** `tox -l` or `tox -a`.
- **Reads:** environments as the union of the expanded `env_list`/`envlist` and
  the explicit `[testenv:NAME]` (INI) or `[tool.tox.env.NAME]` (TOML) sections,
  excluding the `[testenv]` base. INI `envlist` is brace- and range-expanded
  (`{py27,py36}-django{15,16}` is a hyphen-joined Cartesian product; `py3{10-14}`
  expands too). TOML `env_list` list items are literal (a top-level string `env_list` is
  split and brace-expanded; `{replace = "env", ...}` items are resolved). With
  no `envlist` the default env is `py`. Missing fields are
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
  provisioning and package environments (`.pkg`, `.tox`, and the names given by
  `provision_tox_env`, `package_env`, `isolated_build_env`, `wheel_build_env`),
  treated as non-runnable.
- **Adoption (information only):** about 1,869 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).

## cargo-make

- **Source:** `Makefile.toml`. **Issue:** #76. **Shipped:** 0.3.0.
- **Run:** `cargo make <name>`. **Never use:** `cargo make --list-all-steps`.
- **Reads:** `[tasks.<name>]` tables; `description`; `command` plus `args`, or
  `script` (a list joined with ` && `) as `definition`.
- **Skipped:** tasks with `private = true` or `disabled = true`, and entries
  that are not tables. `alias` is not resolved.
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
- **Skipped:** tasks with `options.internal: true`, which cannot be run from
  the CLI.

## VS Code tasks

- **Source:** `.vscode/tasks.json`. **Issue:** #139. **Spec:**
  <https://code.visualstudio.com/docs/editor/tasks>.
- **Run:** the task's own `command` and `args`.
- **Reads:** schema `version` `"2.0.0"`, JSONC. Task `label` is the name,
  `detail` the description. `shell`, `process` and untyped tasks with a
  `command` are runnable. A task or the document may carry `windows`, `linux` and
  `osx` override objects, merged for the current platform; `command` and `args`
  items may be strings or `{ "value", "quoting" }`.
- **Also read:** a document-level `command` is inherited by commandless tasks,
  with its global `args` and the `suppressTaskName`/`taskSelector` rules.
- **Left out:** the legacy `"0.1.0"` schema; any task whose `options` set `env`
  or `shell`, or whose `cwd` resolves anywhere other than the project root
  (the whole task is skipped); extension-contributed task types
  (`npm`, `typescript`, `gulp`, `grunt`, `cargo`, ...), which the extension
  resolves at runtime; any task with a non-empty `dependsOn` (even one that also has its own
  `command`); editor-only variables
  (`${file}`, `${selectedText}`, `${input:...}`): a task whose command, args or
  working directory still contains an unresolved variable is **skipped**, not
  listed (`${workspaceFolder}`, `${workspaceRoot}`, `${workspaceFolderBasename}`,
  `${pathSeparator}`, `${/}` and `${env:X}` resolve). Tasks with `hide: true`
  are skipped. Document-level `options` (including `env`) and `type` merge
  into each task. For duplicate labels the last definition wins.
- **Sibling:** Zed `.zed/tasks.json`, see [candidates](candidates.md).

## invoke

- **Source:** `tasks.py`. **Issue:** #140. **Docs:** <https://docs.pyinvoke.org/en/stable/>.
- **Run:** `invoke <name>`. **Never use:** `inv -l` (imports and executes the module).
- **Reads:** AST of `@task`-decorated functions, including `@task(...)`,
  `@invoke.task` and aliased imports; explicit `name=` and `aliases=`; first
  docstring line as `description`. The module is never imported.
- **Notes:** only top-level `def`s with one decorator are read; underscores in
  names become dashes. A task with a non-empty `pre=`/`post=` is dropped (empty collections are
  accepted), and invalid task
  metadata or colliding `default=True` tasks make the whole file list nothing.
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
  sessions, command bodies. If the module is not straight-line (loops, `with`,
  `match`, unrecognized star imports, namespace mutation; `from nox import *` is
  modeled), the provider lists nothing for the
  file. Only top-level `def`s register sessions; `nox_uv.session` is accepted.

## Apache Ant

- **Source:** `build.xml`. **Issue:** #141. **Docs:** <https://ant.apache.org/manual/>.
- **Run:** `ant <target>`. **Never use:** `ant -p`.
- **Reads:** `<target name=...>`, direct-child `<extension-point name=...>`
  elements (both run as `ant <name>`) and `description`. `depends` ordering and
  `<project default>` are not read (an evaluation idea, not shipped). Declarative XML, so the safest of the imperative-adjacent set.
  Undescribed targets are listed even though Ant hides them from `-p`. Only
  direct children of `<project>` are read; names starting with `-`, files with a
  `<!DOCTYPE>` and non-`<project>` roots are skipped.
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
  description (its last paragraph); the last fenced block under the heading as
  `definition`; heading args `(pattern)` or `[optional]` stripped from the name.
  Untagged or empty fences, and `powershell`, `batch` and `cmd` fences on
  non-Windows, make a command unrunnable. A top-level title or prose heading with no code block or
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
