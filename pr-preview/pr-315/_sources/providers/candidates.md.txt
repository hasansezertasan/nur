# Candidates

Formats not yet shipped. **Accepted** means all three gates of
[ADR 0005](../adr/0005-completeness-over-popularity.md) pass. **Pending** means a
gate is unresolved or the evaluation is incomplete; it is neither accepted nor
declined. They were previously parked on niche adoption or fit, which is no
longer a ground. Each moves to [implemented](implemented.md) when it ships.
[ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md) settled the
Procfile runner, accepted Gradle as a labelled subset and accepted Zed tasks, so
nothing is pending today.

## Procfile

- **Format:** the process-declaration format used by
  [foreman](https://github.com/ddollar/foreman),
  [honcho](https://github.com/nickstenning/honcho),
  [overmind](https://github.com/DarthSim/overmind), Hivemind and Heroku or
  Dokku-style platforms.
- **Source files:** `Procfile` (root). `Procfile.dev` and similar by convention.
- **Docs:** <https://devcenter.heroku.com/articles/procfile>
- **State:** Accepted ([ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md)).
  Delegates to honcho. Issue #145.

**How it is used:**

One process per line, `name: command`:

```text
web: gunicorn app:app --bind 0.0.0.0:$PORT
worker: celery -A app worker
release: python manage.py migrate
```

Each entry is a process type. Run all with `foreman start`, `honcho start` or
`overmind start`; run one with `honcho start <name>` or `foreman start <name>`.
`$PORT` and other variables are substituted by the manager at launch.

**Gate check:**

1. **Safe listing:** passes. A flat `name: command` text file; nothing runs.
2. **Faithful run command:** passes with honcho: `honcho -f Procfile start <name>`. The
   entries are long-running processes rather than one-shot tasks, and the
   managers differ, so the runner was chosen deliberately (see the decision
   below).
3. **Honest subset:** passes. `name` and the full `command` are literal; only
   `$PORT` and environment interpolation is left literal.

**Task-model mapping:**

- `prefix` ← `procfile`
- `name` ← process-type name (left of `:`)
- `argv_base` ← `("honcho", "-f", "Procfile", "start", name)`; `-f` pins the file
  that was parsed, because honcho otherwise lets a `PROCFILE` value in `.env`
  override the default file name
- `description` ← none (the format has no description field)
- `definition` ← the command string (right of `:`)
- `source_file` ← `Procfile`

**Decision: honcho, as a fixed default.**

The delegated command is `honcho -f Procfile start <name>`, chosen in
[ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md) with the
comparison of foreman, overmind and hivemind recorded there. Validated on
2026-10-14 with honcho 2.0.0 and a three-entry Procfile:

- `honcho start worker` ran only that process type and exited 0.
- A process exiting with status 3 made `honcho start` exit 3, so failures reach
  the caller.
- An unknown type was rejected: `Process type 'nosuch' does not exist in
  Procfile`.
- A process referencing `$PORT` received 5000, honcho's default.
- With `PROCFILE=Procfile.dev` in `.env`, plain `honcho start web` ran the
  entry from `Procfile.dev`; `honcho -f Procfile start web` ran the entry from
  `Procfile`, the file `nur` parsed.
- Extra arguments after the name are read as more process types, so forwarded
  arguments must not be passed through (see the acceptance criteria).

Acceptance criteria for the implementation:

- Discovery reads only `Procfile` in the current directory; each non-comment
  `name: command` line is a task; a malformed line is skipped, not guessed.
- Never run honcho or any manager to list.
- Do not forward extra arguments. `Task.run_argv` appends them unconditionally,
  so `nur procfile:web -- worker` would run `honcho -f Procfile start web worker` and start a
  second process type. The implementation must reject or drop extras (a model
  option if needed), with a test.
- The listing and docs say the run needs honcho installed and that process types
  are long-running.

**Deferred:**

`$VAR` and `$PORT` substitution, `.env` and concurrency options (`-c web=2`),
manager-specific extensions, automatic manager selection, `Procfile.dev` and
other variants (`honcho -f`).

**Adoption (information only):**

Common in Heroku-era web apps and local multi-process development. No census
figure is known and none is invented.

## JBang catalog

- **Format:** [JBang](https://www.jbang.dev/) alias catalog, named script aliases
  for the JVM scripting runner.
- **Source files:** `jbang-catalog.json`.
- **Docs:** <https://www.jbang.dev/documentation/guide/latest/alias_catalogs.html>
- **State:** Accepted. Tracked with argc in #146; implement independently.

**How it is used:**

```json
{
  "aliases": {
    "hello":   { "script-ref": "hello.java", "description": "Say hello" },
    "release": { "script-ref": "scripts/release.java" }
  }
}
```

Run an alias with `jbang <alias>`. `script-ref` points at a `.java`, `.jsh` or
URL; `description` is optional.

**Gate check:**

1. **Safe listing:** passes. Plain JSON parse.
2. **Faithful run command:** passes (`jbang <alias>`).
3. **Honest subset:** passes. Remote or nested catalog imports can be deferred.

**Task-model mapping:**

`prefix` ← `jbang`; `name` ← alias key under `aliases`; `argv_base` ←
`("jbang", name)`; `description` ← alias `description`; `definition` ← the
`script-ref`; `source_file` ← `jbang-catalog.json`.

**Deferred:**

Catalog `catalogs` and remote imports; `run` options and per-alias JVM
arguments.

**Adoption (information only):**

No figure known; none invented.

## argc

- **Format:** [argc](https://github.com/sigoden/argc), a Bash-based command
  runner and CLI framework.
- **Source files:** `Argcfile.sh`.
- **Docs:** <https://github.com/sigoden/argc>
- **State:** Accepted. Tracked with JBang in #146; implement independently.

**How it is used:**

Subcommands are declared with comment annotations that argc reads without
executing the bodies:

```sh
#!/usr/bin/env bash
set -e

# @cmd Build the project
# @option --target[=debug]  Build target
build() {
    cargo build --target "$argc_target"
}

# @cmd Run tests
test() {
    cargo test
}

eval "$(argc --argc-eval "$0" "$@")"
```

`# @cmd <desc>` above a function declares a subcommand; `@option`, `@flag` and
`@arg` declare its parameters. Run with `argc build`.

**Gate check:**

1. **Safe listing:** passes by comment scan. **Never use `argc --argc-eval`**,
   which executes the script, and never execute the `eval` footer.
2. **Faithful run command:** passes (`argc <name>`).
3. **Honest subset:** passes. The function body is shell and stays opaque.

**Task-model mapping:**

`prefix` ← `argc`; `name` ← the function name paired with a `# @cmd`;
`argv_base` ← `("argc", name)`; `description` ← the `# @cmd` text;
`definition` ← empty (the body is opaque shell); `source_file` ← `Argcfile.sh`.

**Parsing edge cases:**

- Pair each `# @cmd` with the next function definition to get the runnable name.
- Only `@cmd`-annotated functions are subcommands; plain shell functions are not
  tasks.
- Nested or aliased subcommands and `@meta` need care.

**Deferred:**

Resolving `@option`, `@flag` and `@arg` into full invocations; subcommand groups;
`@meta` directives.

**Adoption (information only):**

No figure known; none invented.

## Zed tasks

- **Format:** Zed editor tasks.
- **Source files:** `.zed/tasks.json`.
- **Docs:** <https://zed.dev/docs/tasks>
- **State:** Accepted ([ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md)).
  A sibling of the VS Code provider, raised in #139.

**How it is used:**

A project file `.zed/tasks.json` holds a JSON array of task objects, run from the
editor's task picker (`task: spawn`). The format is not VS Code's: a bare array
with `label`, `command`, `args`, `env`, `cwd`, `use_new_terminal`,
`allow_concurrent_runs`, `reveal`, `hide`, `shell`, `show_summary`,
`show_command`, `save`, `tags` and `hooks`.

```json
[
  {
    "label": "Run tests",
    "command": "cargo",
    "args": ["test", "--workspace"]
  }
]
```

Variables use `$VAR` or `${VAR:default}` in `label`, `command`, `args` and `cwd`
(`ZED_FILE`, `ZED_WORKTREE_ROOT`, `ZED_SELECTED_TEXT` and others). Zed filters
out a task whose variables are unset when the list is built. There is a global
`~/.config/zed/tasks.json` too. No command-line way to run a task by label is
documented.

**Gate check:**

1. **Safe listing:** passes. A JSON document; nothing runs.
2. **Faithful run command:** passes the same way as VS Code shell tasks: the
   delegated run is the task's own `command` plus `args`, run through the shell,
   because Zed offers no CLI to run a task by label and itself launches the
   command in a login shell (so `$VAR`, pipes and loops work).
3. **Honest subset:** passes. `label`, `command` and `args` are literal.

**Task-model mapping:**

- `prefix` ← `zed`
- `name` ← `label`
- `argv_base` ← `command` verbatim as the first token, then each `args` item;
  `run_in_shell=True`, so the command string may use shell syntax and later
  tokens are quoted as single arguments (as for VS Code `shell` tasks)
- `description` ← none (Zed has no description field)
- `definition` ← the command and args joined with spaces
- `source_file` ← `.zed/tasks.json`
- `passthrough_prefix` ← none
- `run_in_shell` ← true

**Acceptance criteria for the implementation:**

- Read `.zed/tasks.json` under the current directory only, never the global
  file (rooted scope, ADR 0004). Parse as JSONC, since the docs show `//`
  comments.
- Skip a task whose `args` still contain a `$` after Zed variables are resolved:
  `nur` quotes every argument after the command, so shell expansion that Zed
  would perform there (for example `$HOME`) would print literally. Shell syntax
  inside `command` itself works because that token is passed to the shell
  verbatim.
- Skip, as VS Code does: tasks whose `command`, `args`, `cwd` or `label` still
  hold an unresolved `$ZED_*` variable without a default; tasks that set `env` or
  `shell`; and any task whose `cwd` resolves anywhere other than the project
  root, because the `Task` model carries no working directory and
  `run_direct` runs from the discovery directory. Resolve `$ZED_WORKTREE_ROOT`.
  `${VAR:default}` resolves to its default.
- Keep tasks that declare `hooks` or `tags`: Zed states that hook tasks "are
  still available from the task modal like any other task", and `tags` only binds
  a task to editor runnables.
- Do not treat Zed's `hide` as VS Code's `hide`: in Zed it controls closing the
  terminal after the run, not visibility in the picker.
- Document the known difference in login-shell handling: Zed launches a task in
  a login shell, while `nur` runs `$SHELL -c <line>` without a login flag
  (`src/nur/core/execution.py`). A task that depends on PATH entries, functions
  or aliases set only by a login profile can behave differently. This is the same
  boundary as VS Code shell tasks.
- Never run Zed or any editor.

**Deferred:**

Global tasks, language-extension tasks, oneshot tasks, `save`, `reveal` and the
other terminal options.

**Adoption (information only):**

No figure known; none invented.

## Gradle

- **Format:** Gradle build scripts, Groovy or Kotlin DSL.
- **Source files:** `build.gradle` or `build.gradle.kts` in the current directory.
  When both exist Gradle loads `build.gradle` and ignores the Kotlin script (its
  current precedence, see gradle/gradle#34110), so the provider reads only that
  one.
- **Docs:** <https://docs.gradle.org/current/userguide/userguide.html>
- **State:** Accepted as a labelled subset
  ([ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md)). It was
  declined by ADR 0005; see "Earlier decline" below. Issue #148 was closed with
  the decline, so the implementation issue is new.

**How it is used:**

Tasks are registered while the build is configured:

```kotlin
tasks.register("hello") { doLast { println("hi") } }  // explicit
plugins { java }  // adds build, test, jar, check, ... at configuration time
```

Run with `gradle <task>` or `./gradlew <task>`.

**Gate check:**

1. **Safe listing:** passes for a text or syntax-tree scan of literal task
   declarations; nothing runs. `gradle tasks` configures the build, which
   executes the scripts, so it is **never used** (nor the Tooling API).
2. **Faithful run command:** passes: `gradle :<task>`, or the wrapper
   `./gradlew :<task>` when the project has one.
3. **Honest subset:** passes as a labelled subset. Literal declarations are
   real project tasks; what is not listed is stated (below).

**Task-model mapping:**

- `prefix` ← `gradle`
- `name` ← the literal task name
- `argv_base` ← `("./gradlew", ":" + name)` when `gradlew` exists in the project
  root (`gradlew.bat` on Windows), else `("gradle", ":" + name)`. The leading
  colon selects the root project's task by path; a bare name is a selector that
  can also run same-named tasks in every subproject of a multi-project build
- `description` ← a literal `description = "..."` (Groovy also
  `description "..."`) in the task's configuration block, if present
- `definition` ← empty: the body is build logic, not a command
- `source_file` ← the parsed build script
- `passthrough_prefix` ← none for the first cut

**Acceptance criteria for the implementation:**

- Read literal declarations at the top level of the root script.
  Groovy: `task name`, `task name { }`, `task name(type: X) { }`,
  `task('name')`, `tasks.register('name')`, `tasks.create('name')`.
  Kotlin: `tasks.register("name")`, `tasks.register<Type>("name")`,
  `tasks.create("name")`, `val name by tasks.registering`,
  `val name by tasks.creating`.
- `tasks.named`, `tasks.withType` and `tasks.configureEach` configure tasks and
  do not declare them; they are not read.
- Skip non-literal names (variables, string templates, loops, conditionals,
  `afterEvaluate`).
- Say, in the provider docs and in the listing text, that this is a list of
  literal declarations and does **not** include plugin-contributed tasks such as
  `build`, `test`, `check`, `jar` or `assemble`.
- Use a bounded parser (the tree-sitter approach of ADR 0003 and 0004 is the
  precedent) with malformed-input tests; ship it through the packaging checks of
  the supported platforms.

**Deferred:**

`settings.gradle(.kts)` and subproject (`include`) wiring, `buildSrc` and
convention plugins, version catalogs, `init` scripts, task dependencies and
types.

**Earlier decline:**

ADR 0005 declined Gradle on gate 3: the tasks people actually run are added by
plugins at configuration time, so a literal scan "yields a tiny, misleading
subset that silently omits the real ones". The maintainer then ruled that a
subset presented unambiguously as incomplete passes the gate as written, which
ADR 0006 records. The real, plugin-resolved task set remains out of reach
without configuring the build; the revisit trigger for a fuller provider is a
machine-readable, plugin-resolved task manifest readable without configuring
the build.

**Adoption (information only):**

About 7,736 repositories (2025 Task Runner Census (aleyan.com), as recorded in issue 78).
