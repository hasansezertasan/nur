# Accepted candidates

Formats that pass the gates of
[ADR 0005](../adr/0005-completeness-over-popularity.md) and are queued for
implementation. They were previously parked on niche adoption or fit; that is no
longer a ground ([ADR 0005](../adr/0005-completeness-over-popularity.md)).
Each moves to [implemented](implemented.md) when it ships.

## Procfile

- **Format:** the process-declaration format used by
  [foreman](https://github.com/ddollar/foreman),
  [honcho](https://github.com/nickstenning/honcho),
  [overmind](https://github.com/DarthSim/overmind), Hivemind and Heroku or
  Dokku-style platforms.
- **Source files:** `Procfile` (root). `Procfile.dev` and similar by convention.
- **Docs:** <https://devcenter.heroku.com/articles/procfile>
- **State:** Accepted. Issue #145.

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
2. **Faithful run command:** passes **once a runner is chosen.** The entries are
   long-running processes rather than one-shot tasks, and foreman, honcho and
   overmind differ, so the delegated command needs a deliberate choice (see
   open design question).
3. **Honest subset:** passes. `name` and the full `command` are literal; only
   `$PORT` and environment interpolation is left literal.

**Task-model mapping:**

- `prefix` ← `procfile`
- `name` ← process-type name (left of `:`)
- `argv_base` ← `<manager> start <name>`, manager per the open question
- `description` ← none (the format has no description field)
- `definition` ← the command string (right of `:`)
- `source_file` ← `Procfile`

**Open design question:**

Which manager to delegate to. Options: a fixed default (honcho), detection of an
installed manager, a project setting, or running the command string directly and
bypassing the manager. Decide in the implementation issue and record it here.
This is a fidelity question (gate 2), not a reason to decline.

**Deferred:**

`$VAR` and `$PORT` substitution, `.env` and concurrency options (`-c web=2`),
manager-specific extensions, automatic manager selection.

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
`definition` ← the same description (body opaque); `source_file` ← `Argcfile.sh`.

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
- **State:** Accepted as a sibling of the VS Code provider. Raised in #139; no
  separate issue yet.

It shares the `label`, `command` and `args` vocabulary of VS Code tasks but has
a different top-level shape (a bare JSON array rather than an object with a
`tasks` field), so it is a separate provider. Evaluate it against the template
when its implementation issue is opened; the VS Code evaluation in
[implemented](implemented.md) is the starting point.
