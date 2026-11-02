# Origin of the register (issue #78)

This page preserves the content of issue #78, "Tracking: researched-but-deferred
provider candidates", so closing the issue loses nothing. It is a historical
record: the verdicts below are the ones given when the issue was written. They
were re-triaged by [ADR 0005](../adr/0005-completeness-over-popularity.md); the
current state of every format is in the [register](index.md).

## Purpose of the issue

The issue indexed candidate provider formats that had been researched. Each had
its own issue with usage detail and a rationale, so the evaluation was not
repeated and the reasoning for building or not building was discoverable.

## Provenance

Most candidates were surfaced by `nur`'s closest analog,
[task-keeper](https://github.com/linux-china/task-keeper), a static multi-runner
covering about 28 formats. They were cross-checked against `nur`'s supported and
filed lists.

## Re-analysis note (from #140)

The original split assumed several formats "cannot be statically parsed". That
was imprecise. The [ADR 0001](../adr/0001-provider-selection-criteria.md) hard
rule is that *listing* must not execute project code, and AST, text and comment
parsing are safe. Task **names** are often statically discoverable even when the
command **body** is imperative, and a delegated run command (`invoke <name>`,
`rake <name>`, and so on) is legitimate. So `invoke`, `nox` and Rake were
candidates, parked on value and reliability rather than safety, while GitHub
Actions, Gradle/Maven, CMake and cargo-xtask were declined on honest, per-format
grounds.

## Graduated to candidate provider issues

Safe to parse; parked on demand or value. The "Verdict" column is the original
one. Every row except Procfile, JBang and argc has since shipped (see the
[register](index.md)).

| Format | Source file | Static basis | Verdict | Issue |
| ------ | ----------- | ------------ | ------- | ----- |
| VS Code / Zed tasks | `.vscode/tasks.json` | JSONC parse; `shell`/`process` tasks are locally runnable | candidate | #139 |
| invoke | `tasks.py` | AST of `@task` names; run `invoke <name>` | candidate (low priority) | #140 |
| Ant | `build.xml` | XML `<target>` names; run `ant <target>` | candidate (low priority) | #141 |
| nox | `noxfile.py` | AST of `@nox.session` names; run `nox -s <name>` | candidate (low priority) | #142 |
| Rake | `Rakefile` | parse literal `task :name` + `desc`; run `rake <name>` | candidate (low priority) | #143 |
| mask | `maskfile.md` | Markdown headings + fenced bodies | candidate (overlaps `xc`) | #144 |
| Procfile | `Procfile` | `name: command` lines | candidate (borderline: process manager) | #145 |
| JBang / argc | `jbang-catalog.json` / `Argcfile.sh` | JSON aliases / `# @cmd` comment scan | candidate (niche) | #146 |

"Overlaps `xc`", "borderline" and "niche" are no longer grounds to park a format
(ADR 0005). Zed tasks were listed with VS Code in #139 and are tracked
separately as pending in the register.

## Declined with documented grounds

The original reasons, per format. The current, gate-based records are in
[declined](declined.md).

| Format | Source file | Why declined | Issue |
| ------ | ----------- | ------------ | ----- |
| GitHub Actions | `.github/workflows/*.yml` | Parses fine, but **not a local task runner** (remote runners, `${{ }}` / matrix / `needs`) and inherently **multi-file** versus the single-file scope. The highest adoption (~39,887) does not override this. | #147 |
| Gradle / Maven | `build.gradle(.kts)` / `pom.xml` | Gradle's task set is plugin- and runtime-produced (no reliable static parse). Maven's runnable units are a fixed universal lifecycle, so `pom.xml` yields nothing project-specific. Gradle ~7,736. | #148 |
| CMake | `CMakeLists.txt` | Imperative build **generator**, not a task runner; target names are variable-, loop- and generator-expression-driven; the two-step configure/build model leaves no clean delegated run. ~6,771. | #149 |
| cargo-xtask | `xtask/` crate | Not a format but a convention; subcommands are arbitrary Rust with no declarative file (`.cargo/config.toml` only proves the alias exists). | #150 |

The multi-file ground for GitHub Actions was retired when ADR 0004 replaced the
single-file scope with a rooted scope.

## Use case

`nur`'s value depends on a hard guarantee: **listing tasks never executes
project code.** As candidate formats are researched, some turn out to be safe to
parse (even if only a subset), and some genuinely do not fit: not a local task
runner (GitHub Actions), no reliable static parse (Gradle, CMake), or no
declarative file at all (cargo-xtask). The issue indexed those decisions with
their rationale so future contributors do not re-litigate them, and so the
boundary of what `nur` will and will not support stays explicit. Candidates can
be built when demand or value warrants; declines can be revisited if
circumstances change.
