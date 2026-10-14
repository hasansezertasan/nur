# Declined formats

Each format here fails a gate of
[ADR 0005](../adr/0005-completeness-over-popularity.md). A decline records a fact
about the format, not a priority call, and each entry names the **revisit
trigger** that would reopen it. Adoption figures are information only.

Where the original evaluation cited the single-file scope of ADR 0001, note that
scope was retired by [ADR 0004](../adr/0004-js-adjacent-task-runners.md); the
grounds below are restated without it.

## GitHub Actions

- **Format:** [GitHub Actions](https://docs.github.com/en/actions) workflows.
- **Source files:** `.github/workflows/*.yml` and `*.yaml`.
- **Docs:** <https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions>
- **State:** Declined. **Failed gate:** 2 (faithful run command). Issue #147.

**How it is used:**

A workflow is YAML that runs on GitHub's (or self-hosted) runners in response to
events. A repository has many files, each with `jobs`, each job a list of
`steps`; a step is either `uses:` (a packaged action) or `run:` (a shell command).
Workflows start from triggers (`on:` events, `schedule` cron, `workflow_dispatch`),
`needs:` chains jobs, and logic can live in reusable or composite workflows in
other files or repositories (reusable workflows) or in composite actions.

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e .
      - run: pytest -q
```

**Gate check:**

1. **Safe listing: passes.** Workflow YAML is data; parsing executes nothing.
   Safety is not the blocker (the earlier "cannot be statically parsed" label
   in #78 was imprecise).
2. **Faithful run command: fails.** `run:` commands routinely embed `${{ … }}`
   expressions (`matrix`, `secrets`, `env`, `github`, `needs`, `vars`) resolved by
   the Actions runner, and presume a provisioned runner (checkout done, actions
   installed, env injected). A step extracted verbatim rarely runs on a laptop
   or means the same thing, so a delegated command cannot reproduce it.
3. **Honest subset:** no useful locally-runnable subset exists, for the same reason.

**Decision:**

Declined on gate 2 alone. It is CI orchestration, not a local task runner.
The multi-file layout that the original record also cited is no longer a ground.

- **Adoption (information only):** about 39,887 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78), the highest of any candidate; it does not change the
  decision.
- **Revisit trigger:** a supported way to run a single job or step locally with
  the same semantics (a local runner that resolves the expression context
  without remote state), and one `nur` could delegate to.

## Gradle

- **Format:** Gradle build scripts, Groovy or Kotlin DSL.
- **Source files:** `build.gradle`, `build.gradle.kts`, with `settings.gradle(.kts)`
  for multi-module builds.
- **Docs:** <https://docs.gradle.org/current/userguide/userguide.html>
- **State:** Declined. **Failed gate:** 3 (honest subset), and gate 1 for real
  enumeration. Issue #148.

**How it is used:**

Tasks are registered imperatively while the build is configured:

```kotlin
tasks.register("hello") { doLast { println("hi") } }  // explicit
plugins { java }  // adds build, test, jar, check, ... at configuration time
```

Run with `gradle <task>` or `./gradlew <task>`. `gradle tasks` lists them by
configuring the build, which executes the scripts and applies plugins.

**Gate check:**

1. **Safe listing:** only a literal text or AST scan for `tasks.register("x")` or
   `task x` avoids execution. Real enumeration needs `gradle tasks`, which fails
   the hard rule.
2. **Faithful run command:** `gradle <task>` is fine once a name is known.
3. **Honest subset: fails.** The tasks users actually run (`build`, `test`,
   `check`, `jar`, `assemble`, and every plugin- or convention-contributed task)
   are added by plugins at configuration time. A literal-name scan yields a tiny,
   misleading subset that silently omits the real ones. DSL forms, dynamic names
   and `settings.gradle` wiring compound this. ADR 0005 allows a documented safe
   subset, so this is a judgment that a subset missing nearly every real task
   is not *honest* even when labelled; see the revisit trigger.

**Decision:**

Declined. Enumerating by running the build breaks the hard rule (the reason
`make` text-parses instead of running `make -pRrq`), and the static subset
misleads.

- **Adoption (information only):** about 7,736 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).
- **Not pursued:** configure-time or `gradle tasks` enumeration; the Tooling API.
- **Revisit trigger:** a machine-readable, plugin-resolved task manifest that can
  be read without configuring the build, or a literal-`register` subset that can
  be presented unambiguously as incomplete and is judged worth shipping.

Gradle and Maven are both build systems rather than task runners. Declining them
is consistent with GitHub Actions (remote orchestration) and CMake (a build-graph
generator). Gradle does let developers declare tasks, but their full set is
produced at configuration time and by plugins, which cannot be enumerated safely.

## Maven

- **Format:** Maven project object model.
- **Source file:** `pom.xml`.
- **Docs:** <https://maven.apache.org/guides/>
- **State:** Declined. **Failed gate:** 3 (honest subset): the provider would
  not enumerate anything project-specific today. Issue #148.

**How it is used:**

`pom.xml` is declarative XML, but Maven is not a named-task runner. You invoke
**lifecycle phases** (`mvn compile`, `test`, `package`, `verify`, `install`,
`deploy`) or **plugin goals** (`mvn spotless:apply`). The phases are identical in
every Maven project; `pom.xml` configures how they behave, not which exist.

**Gate check:**

1. **Safe listing:** passes, `pom.xml` is XML.
2. **Faithful run command:** passes (`mvn <phase>`).
3. **Honest subset: fails today.** Configured plugin goals are project-specific,
   but extracting them is not done (see below), so nothing project-distinct is
   discovered. The runnable phases
   are a fixed universal list `nur` could hard-code without reading any file,
   which is not discovery. Plugin `<executions>` bind goals to phases that still
   run via `mvn <phase>`, and correctness needs `<parent>`, `<profiles>` and
   effective-POM resolution.

**Decision:**

Declined: a provider would emit the same roughly seven phases everywhere.

- **Adoption (information only):** no figure recorded; none is assumed.
- **Not pursued:** effective-POM resolution, profiles, parent inheritance,
  plugin-goal binding extraction.
- **Revisit trigger:** a use case for surfacing configured plugin goals
  (for example formatter or release goals declared in the POM) as tasks, with a
  documented resolution boundary.

## CMake

- **Format:** CMake, a meta-build system that generates native build files
  from an imperative scripting language.
- **Source file:** `CMakeLists.txt` (one per directory, linked by
  `add_subdirectory()`).
- **Docs:** <https://cmake.org/cmake/help/latest/>, commands:
  <https://cmake.org/cmake/help/latest/manual/cmake-commands.7.html>
- **State:** Declined. **Failed gates:** 2 (faithful run command) and 3 (names
  are runtime products). Issue #149.

**How it is used:**

```cmake
cmake_minimum_required(VERSION 3.20)
project(demo)
add_executable(app main.c)                 # build target "app"
add_custom_target(docs COMMAND doxygen)    # custom target "docs"
enable_testing()
add_test(NAME unit COMMAND app --test)     # ctest test "unit"
add_subdirectory(lib)
```

Usage is two-step: configure once (`cmake -B build`, which runs the script and
generates the build system), then build a target
(`cmake --build build --target app`). Tests run via `ctest`. There is no
`cmake <target>` one-shot like `ant <target>`. CMake generates native build files
(Makefiles, Ninja, IDE projects), so a build can also run as `make app` or
`ninja app` in the build directory.

**Gate check:**

1. **Safe listing:** a text scan for `add_executable(` or `add_custom_target(`
   does not execute, but reliable enumeration needs configuration
   (`cmake -B build`, then `cmake --build ... --target help`), which runs the
   project script and fails the hard rule.
2. **Faithful run command: fails.** The configure and build split means no
   delegated `("cmake", name)` works without a prior configure step.
3. **Honest subset:** target names come from `${VARIABLES}`, `foreach()` loops,
   `function()` or `macro()` wrappers and generator expressions (`$<...>`), all
   resolved only when CMake runs. A literal scan is incomplete and often wrong.

**Decision:**

Declined. It is a build-graph generator whose primary act is "generate a build
system", not "run task X".

- **Adoption (information only):** about 6,771 repositories (2025 Task Runner Census (aleyan.com), as recorded in #78).
- **Not pursued:** configure-based enumeration, the File API, resolving
  variable, loop or generator-expression names, `add_subdirectory()` aggregation.
- **Revisit trigger:** a stable one-step invocation that builds a named target
  without a manual configure, together with a name source that needs no
  evaluation. CMake presets (`CMakePresets.json`) are a separate declarative
  file worth evaluating on their own.

## cargo-xtask

- **Format:** a Rust convention for writing project automation in an `xtask/`
  crate, not a tool or a file format.
- **Source files:** none that is declarative. The only static artifact is a
  `cargo` alias in `.cargo/config.toml`.
- **Docs:** <https://github.com/matklad/cargo-xtask> (the convention originated
  with matklad), README: <https://github.com/matklad/cargo-xtask/blob/master/README.md>
- **State:** Declined. **Reason:** no declarative file; gate 3. Issue #150.

**How it is used:**

```toml
# .cargo/config.toml, the only static artifact
[alias]
xtask = "run --package xtask --"
```

```rust
// xtask/src/main.rs: arbitrary Rust, subcommands hand-parsed
fn main() -> Result<(), anyhow::Error> {
    match std::env::args().nth(1).as_deref() {
        Some("dist")    => dist()?,
        Some("codegen") => codegen()?,
        _               => print_help(),
    }
    Ok(())
}
```

`cargo xtask` expands to `cargo run --package xtask --`, compiling and running a
normal Rust binary. Subcommands are whatever `main` accepts (a `match` on
`args()`, `clap`, `argh`, `pico-args`, and so on). `cargo xtask --help` works
only if the binary implements it, and the first run, or any run after a change,
compiles the crate first.

**Gate check:**

1. **Safe listing:** reading `.cargo/config.toml` executes nothing but only shows
   that the alias exists. Reliable enumeration means `cargo xtask --help`, which
   compiles and runs the crate.
2. **Faithful run command:** passes once a name is known (`cargo xtask <name>`).
3. **Honest subset: fails.** Names are string literals buried in arbitrary Rust
   with no guaranteed shape; grepping `match` arms or `clap` `.subcommand("...")`
   calls is heuristic and breaks on any non-trivial setup. There is nothing
   standardized to target.

**Decision:**

Declined: it is a convention, so there is no file format to parse.

- **Adoption (information only):** no census figure; used by rust-analyzer among
  others. A convention is also hard to count, so #78 did not enumerate it.
- **Revisit trigger:** a conventional, declarative description of xtask commands
  that projects adopt, or a bounded subset (for example a `clap` derive enum)
  judged reliable enough to ship as syntactic candidates.
