# Provider register

This section is the permanent record of every task-runner format `nur` has
considered: what it is, how it was evaluated, what was decided and why.
It exists so the reasoning never depends on GitHub issues. The selection rule
is [ADR 0005](../adr/0005-completeness-over-popularity.md): a format is accepted
when it passes three gates (safe listing, a faithful run command, an honest
subset). Popularity is not a gate.

`nur` is for developers and agents working in a terminal, so the goal is to
cover every format that can be supported honestly.

The register began as issue #78; its content is preserved in
[Origin of the register](history.md).

## How to read the register

| State | Meaning |
| ----- | ------- |
| **Implemented** | A provider ships. Evaluation kept in [implemented](implemented.md). |
| **Accepted** | Passes all three gates; implementation not yet done. See [candidates](candidates.md). |
| **Pending** | Not yet accepted: a gate is unresolved or evaluation is incomplete. See [candidates](candidates.md). |
| **Declined** | Fails a gate. Reason and revisit trigger in [declined](declined.md). |
| **Research** | A cross-cutting investigation with measured results, under [research](research/markdown-parser.md). |

The eight providers that predate the register (npm, make, pdm, poe, just,
Taskfile, mise, xc) were backfilled in [implemented](implemented.md). ADR 0005
still says that backfilling is open work: ADRs are immutable once accepted, so
this page is where its completion is recorded.

Add a register entry, using the [evaluation template](template.md), before
opening an implementation issue. A provider PR updates its entry in the same
change.

## Every format considered

| Format | Source file | State | Issue |
| ------ | ----------- | ----- | ----- |
| npm scripts | `package.json` | Implemented | |
| make | `Makefile` | Implemented ([ADR 0003](../adr/0003-bounded-make-discovery.md)) | |
| pdm | `pyproject.toml` | Implemented | |
| poe | `pyproject.toml` | Implemented | |
| just | `justfile` | Implemented | #91 |
| Taskfile (`task`) | `Taskfile.yml` / `Taskfile.yaml` | Implemented | |
| mise | `mise.toml` and variants | Implemented | #32 |
| xc | `README.md` | Implemented | #36 |
| deno | `deno.json` / `deno.jsonc` | Implemented (0.3.0) | #71 |
| composer | `composer.json` | Implemented (0.3.0) | #72 |
| pre-commit | `.pre-commit-config.yaml` | Implemented (0.5.0) | #73 |
| tox | `tox.ini` / `setup.cfg` / `pyproject.toml` / `tox.toml` | Implemented (0.5.0) | #74 |
| cargo-make | `Makefile.toml` | Implemented (0.3.0) | #76 |
| moon | `moon.yml` | Implemented (0.4.0) | #77 |
| VS Code tasks | `.vscode/tasks.json` | Implemented | #139 |
| invoke | `tasks.py` | Implemented | #140 |
| Apache Ant | `build.xml` | Implemented | #141 |
| nox | `noxfile.py` | Implemented | #142 |
| Rake | `Rakefile` | Implemented ([ADR 0002](../adr/0002-bounded-rake-discovery.md)) | #143 |
| mask | `maskfile.md` | Implemented | #144 |
| Grunt | `Gruntfile.js` | Implemented ([ADR 0004](../adr/0004-js-adjacent-task-runners.md)) | #75 |
| Gulp | `gulpfile.js` | Implemented (ADR 0004) | #75 |
| Turborepo | `turbo.json` | Implemented (ADR 0004) | #75 |
| Nx | `nx.json` | Implemented (ADR 0004) | #75 |
| pnpm / yarn / bun workspaces, Lerna | `pnpm-workspace.yaml`, `package.json`, `lerna.json` | Implemented as `workspace` (ADR 0004) | #75 |
| npm-run-all / run-p / run-s | none | No provider needed: composes `package.json` scripts the `npm` provider already lists | #75 |
| Procfile | `Procfile` | **Accepted** ([ADR 0006](../adr/0006-accept-gradle-zed-and-procfile-runner.md)): delegates to `honcho -f Procfile start <name>` | #145 |
| JBang catalog | `jbang-catalog.json` | **Accepted** | #146 |
| argc | `Argcfile.sh` | **Accepted** | #146 |
| Zed tasks | `.zed/tasks.json` | **Accepted** (ADR 0006); sibling of VS Code tasks | #139 |
| GitHub Actions | `.github/workflows/*.yml`, `*.yaml` | Declined (gate 2) | #147 |
| Gradle | `build.gradle(.kts)` | **Accepted** as a labelled subset (ADR 0006); declined by ADR 0005 | #148 |
| Maven | `pom.xml` | Declined (no project-specific tasks) | #148 |
| CMake | `CMakeLists.txt` | Declined (gate 2) | #149 |
| cargo-xtask | `xtask/` crate | Declined (no declarative file) | #150 |

## Cross-cutting research

| Topic | State | Page |
| ----- | ----- | ---- |
| Broader tree-sitter adoption for existing providers (#292) | `make` adopted ([ADR 0003](../adr/0003-bounded-make-discovery.md)); `just` open (#294); Markdown open (#302) | [Markdown parser evaluation](research/markdown-parser.md) |

```{toctree}
:maxdepth: 1
:hidden:

template
implemented
candidates
declined
history
research/markdown-parser
```
