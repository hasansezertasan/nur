# nur

[![CI](https://github.com/hasansezertasan/nur/actions/workflows/ci.yml/badge.svg)](https://github.com/hasansezertasan/nur/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/codecov/c/github/hasansezertasan/nur)](https://codecov.io/gh/hasansezertasan/nur)
[![Documentation Status](https://img.shields.io/github/deployments/hasansezertasan/nur/github-pages?label=docs)](https://hasansezertasan.github.io/nur)
[![PyPI - Version](https://img.shields.io/pypi/v/nur.svg)](https://pypi.org/project/nur)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/nur.svg)](https://pypi.org/project/nur)
[![License - MIT](https://img.shields.io/github/license/hasansezertasan/nur.svg)](https://opensource.org/licenses/MIT)
[![GitHub Stars](https://img.shields.io/github/stars/hasansezertasan/nur?style=social)](https://github.com/hasansezertasan/nur/stargazers)
[![Latest Commit](https://img.shields.io/github/last-commit/hasansezertasan/nur)](https://github.com/hasansezertasan/nur)

[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![linting - Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/charliermarsh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/hasansezertasan/nur/badge)](https://scorecard.dev/viewer/?uri=github.com/hasansezertasan/nur)
[![GitHub Tag](https://img.shields.io/github/tag/hasansezertasan/nur?include_prereleases=&sort=semver&color=black)](https://github.com/hasansezertasan/nur/releases/)

[![Downloads](https://pepy.tech/badge/nur)](https://pepy.tech/project/nur)
[![Downloads/Month](https://pepy.tech/badge/nur/month)](https://pepy.tech/project/nur)
[![Downloads/Week](https://pepy.tech/badge/nur/week)](https://pepy.tech/project/nur)

> A script discovery and execution engine for your project's tasks.

-----

Run `nur` in a project and it discovers the tasks your project already defines —
from npm, Make, deno, composer, just, Taskfile, pre-commit, PDM/poe, tox, mise,
cargo-make, moon, xc, VS Code tasks, nox, mask, Invoke, and Rake — then lets you run them from a TUI picker or directly from the command line.
Discovery is limited to the current directory. See [Features](#features) for the
full list of source files.

## Table of Contents

- [Installation](#installation)
- [Usage](#usage)
- [Support](#support-heart)
- [Motivation](#motivation)
- [Features](#features)
- [Author](#author-person_with_crown)
- [Analysis](#analysis)
- [Contributing](#contributing-heart)
- [Development](#development-toolbox)
- [Releasing](#releasing)
- [Credits](#credits)
- [License](#license-scroll)
- [Changelog](#changelog-memo)

## Installation

`nur` is a standalone command-line tool — install it into an isolated
environment rather than as a project dependency:

```console
uv tool install nur
```

Or run it without installing:

```console
uvx nur
```

On macOS/Linux via [Homebrew](https://github.com/hasansezertasan/homebrew-tap):

```console
brew install hasansezertasan/tap/nur
```

On Windows via [Scoop](https://github.com/hasansezertasan/scoop-bucket):

```console
scoop bucket add hasansezertasan https://github.com/hasansezertasan/scoop-bucket
scoop install nur
```

To install the latest unreleased changes from source:

```console
uv tool install git+https://github.com/hasansezertasan/nur
```

Or, from a clone:

```console
uv tool install .
```

### Verify the installation

The package root is importable after installation:

```pycon
>>> import nur
>>> isinstance(nur.__version__, str)
True

```

## Usage

Run `nur` from the root of a project that contains any supported task file.

### TUI

Run with no arguments to open the interactive picker:

```bash
nur
```

Browse and run the discovered tasks in a three-pane Textual UI. Press `q` to
exit.

### CLI

```bash
nur test            # run a task by name (if unambiguous)
nur make:test       # run a task by its qualified name
nur test -- --watch # pass extra args through to the underlying runner
nur list            # print all discovered tasks
nur --version
```

## Support :heart:

If you have any questions or need help, feel free to open an issue on the [GitHub repository][nur].

## Motivation

Every project speaks a different task dialect — `make test`, `npm run test`,
`just test`, `task test`, `pdm run test`, `poe test`, `tox -e test`, `mise run test`, `xc test`. nur gives
you one command that discovers whatever a project already uses and runs it, with no config and
no need to remember which runner lives where. Discovery is pure text/JSON/TOML
parsing, so listing tasks never executes anything (no `make -pRrq` side effects).

## Features

- **Zero-config discovery** across nineteen providers, each parsed from a single
  source file in the current directory:

  | Provider | Prefix | Source file |
  | --- | --- | --- |
  | npm | `npm` | `package.json` |
  | deno | `deno` | `deno.json` / `deno.jsonc` |
  | composer | `composer` | `composer.json` |
  | Make | `make` | `Makefile` |
  | PDM | `pdm` | `pyproject.toml` (`[tool.pdm.scripts]`) |
  | poe | `poe` | `pyproject.toml` (`[tool.poe.tasks]`) |
  | tox | `tox` | `tox.ini`, `setup.cfg`, `pyproject.toml`, or `tox.toml` |
  | just | `just` | `justfile` |
  | Taskfile | `task` | `Taskfile.yml` |
  | pre-commit | `pre-commit` | `.pre-commit-config.yaml` |
  | mise | `mise` | `mise.toml` (and variants — see below) |
  | cargo-make | `cargo-make` | `Makefile.toml` |
  | moon | `moon` | `moon.yml` |
  | xc | `xc` | `README.md` (see below) |
  | VS Code | `vscode` | `.vscode/tasks.json` (see below) |
  | Invoke | `invoke` | `tasks.py` (see below) |
  | nox | `nox` | `noxfile.py` (see below) |
  | mask | `mask` | `maskfile.md` (see below) |
  | Rake | `rake` | `Rakefile` (see below) |

  `rake` parses `Rakefile` with Tree-sitter without running Ruby or `rake -T`.
  It discovers direct literal `task` and `multitask` declarations, including
  dependency-only tasks, and tracks nested literal `namespace` blocks.
  Calls may use an explicit `self` receiver.
  A pending ordinary quoted `desc` supplies the next task description,
  including across unrelated statements and namespaces. For example,
  `task :migrate` inside `namespace :db` runs as `rake db:migrate` and is
  available as `nur rake:db:migrate`.
  Task bodies stay opaque, so `definition` is empty. Conditional declarations,
  loops, helper methods, dynamic names/namespaces, file tasks, rules, imported
  files and `rakelib/*.rake` are skipped. Escaped/interpolated names, percent
  literals and heredocs are omitted; direct singleton redefinitions disable
  the affected DSL methods. Task names must contain only letters,
  digits, underscores, colons, dots, slashes and hyphens, starting with a letter,
  digit or underscore. Names beginning with the reserved `rake:` lookup prefix
  and task names ending in a colon are skipped. Files with Ruby syntax errors
  reported by the parser are skipped
  with a warning.
  Explicit task-name dependency hashes are supported.
  Files that directly undefine a Rake DSL method are skipped with a warning.
  Statically invalid task or namespace calls and descriptions of a known
  invalid type (such as symbols or numbers) cause the file to be skipped.
  Direct declarations in active literal `BEGIN` initializers are read
  before ordinary statements, matching Ruby initialization order.
  Regexp literal checks support ASCII patterns with ordinary groups,
  character classes, anchors and quantifiers.
  Files with complex or encoding-sensitive regexp literals
  (such as named groups, lookbehinds or Unicode property escapes)
  are skipped with a warning.
  Interpolated regexps evaluated during loading also cause a file to be skipped;
  interpolated regexps in deferred task or method bodies stay opaque.

  `tox` reads the first applicable config file present, in priority order:
  `tox.ini`, `setup.cfg` (`[tox:tox]`), `pyproject.toml` (`[tool.tox]`), then
  `tox.toml`. It statically parses environments and never runs `tox` during
  discovery. `mise` reads the first config file present, in priority order:
  `mise.local.toml`, `mise.toml`, `.mise.local.toml`, `.mise.toml`,
  `.config/mise.toml`. `xc` reads its task section from `README.md` — the block
  marked with an `<!-- xc-heading -->` comment, or failing that a heading named
  `Tasks` (which is how nur discovers the tasks documented in this very file).
  `composer` reads the top-level `scripts` object from `composer.json`, surfacing
  only user-defined custom scripts (Composer's reserved lifecycle hooks like
  `post-install-cmd` are filtered out) and taking descriptions from the
  `scripts-descriptions` table when present.
  `pre-commit` reads only `repos[].hooks[]` and runs a selected hook with
  `pre-commit run <id> --all-files`; it never invokes `pre-commit` during discovery.
  `vscode` reads version `2.0.0` `.vscode/tasks.json` (JSONC) and surfaces
  `shell` and `process` tasks with a `command` (an untyped task runs as a
  process, as in VS Code), applying the current platform's overrides, inheriting
  the document-level `type` and, for tasks without their own `command`, its
  `command` and `args`, and resolving `${workspaceFolder}`, `${workspaceFolderBasename}`,
  `${pathSeparator}`, and `${env:NAME}`. Tasks nur cannot run faithfully are
  skipped: hidden tasks, tasks with a non-empty `dependsOn`, other variables, or `options`
  setting `env`, `shell`, or a `cwd` other than the project root. Shell tasks run
  through `$SHELL -c` on POSIX (falling back to `/bin/sh`) and `cmd.exe` on
  Windows; an object-form `command` is quoted as a single literal token.

  `invoke` parses `tasks.py` as a Python syntax tree without importing it or
  running `inv --list`. It discovers top-level functions with a single `@task`,
  `@task(...)`, or `@invoke.task` decorator, including import aliases. Literal
  `name=` and list/tuple `aliases=` are supported; underscores become dashes
  following Invoke's default configuration. Tasks run as `invoke <name>` with
  extra CLI flags passed through, and the first docstring line is the description.
  Command bodies, required parameters, pre/post hooks, imported or dynamic tasks,
  conditional definitions, custom decorators, and explicit `Collection` wiring
  are not resolved. Explicit namespaces may therefore make the discovered bare
  names unavailable, and configuration disabling automatic dashes is not read.
  Names and aliases containing dots or beginning with a dash are excluded because
  Invoke interprets them as namespace paths or CLI options.

  `nox` parses `noxfile.py` as a Python syntax tree (never importing it, unlike
  `nox --list`) and surfaces top-level `@nox.session`-decorated functions,
  runnable as `nox -s <name>`, using an explicit string `name=` when given and
  the docstring's first line as the description. `nox-uv`'s drop-in `session`
  decorator is recognised too, and `python=[...]` / `@nox.parametrize`
  variants appear under their base name (which runs every variant). Only
  straight-line noxfiles are trusted: module code may use imports,
  assignments, `def`/`class`, and `if`/`try` blocks made of those (such as
  try-import fallbacks and `if sys.version_info < ...: raise` guards);
  `if TYPE_CHECKING:` and `if __name__ == "__main__":` blocks are skipped. A
  noxfile that uses loops, `with`, `match`, `assert`, `raise` inside a `try`,
  or that edits `nox.session` or a namespace directly lists no sessions, rather
  than sessions nox may not register.
  Discovery is best-effort: it cannot know whether an import or an expression
  fails in nox's environment, and deliberately unusual module code (aliasing
  tricks, handler matching inside `try`, and similar) may still list a session
  nox rejects. Running such a session fails with nox's own error; nur never runs
  noxfile code to discover sessions.
  `mask` reads `maskfile.md` the way mask does: each heading after the title is
  a command, a deeper heading is its subcommand, the last fenced block under a
  heading is its script, and a `>` blockquote is its description. A subcommand
  is named by its path and runs with it, so `### test lint` under `## test` is
  the task `test lint` (`nur "test lint"` runs `mask test lint`). Task names
  are the shell words after `mask`, so one command whose own name has a space
  is quoted: `## deploy prod` is the task `'deploy prod'`. A heading
  becomes a task only when its last code block is fenced with a language tag,
  since mask needs the tag to pick an interpreter; `powershell`, `batch`, and
  `cmd` blocks count only on Windows, as in mask. Headings and fences inside
  HTML blocks (comments, `<details>`, and the like) are ignored. Fences nested
  in blockquotes are not recognised. Like mask, a heading's inline markup
  restarts its name, so `## a *b* c` is the task `b c`.
- **CLI Application**: run any discovered task by name or qualified `prefix:name`, with `--` passthrough to the underlying runner.
- **TUI Application**: interactive three-pane task picker built with Textual.
- **Safe by default**: discovery parses files; it never shells out to a runner just to list tasks.
- **Type Safety**: full type hints checked by mypy, basedpyright, ty, pyrefly, and zuban.
- **Code Quality**: comprehensive linting and formatting with ruff, plus architecture-contract enforcement with import-linter.
- **Testing**: pytest with coverage reporting and parallel execution.
- **Documentation**: Sphinx documentation with the Shibuya theme, GitHub Pages deployment, and live per-PR documentation previews.
- **CI/CD**: automated testing, building, and publishing across multiple platforms.
- **Security**: CodeQL, OpenSSF Scorecard, dependency review, secret scanning (gitleaks), dependency auditing (pip-audit), GitHub Actions static analysis (zizmor — a blocking prek/CI gate plus a Security-tab dashboard, over hardened least-privilege workflows), and a CycloneDX SBOM attached to every release.
- **Managed `.gitignore`**: kept in sync with the upstream [github/gitignore](https://github.com/github/gitignore) templates by [cobo](https://github.com/hasansezertasan/cobo), with a weekly drift check.
- **Modern Python**: uv for dependency management, hatch for building.

## Author :person_with_crown:

This project is maintained by [Hasan Sezer Taşan][author], It's me :wave:

## Analysis

- [Snyk Python Package Health Analysis](https://snyk.io/advisor/python/nur)
- [Libraries.io - PyPI](https://libraries.io/pypi/nur)
- [Safety DB](https://data.safetycli.com/packages/pypi/nur)
- [PePy Download Stats](https://www.pepy.tech/projects/nur)
- [PyPI Download Stats](https://pypistats.org/packages/nur)
- [Pip Trends Download Stats](https://piptrends.com/package/nur)
- [PyPI Map Dependency Graph](https://pypimap.com/package/nur)

## Contributing :heart:

Any contributions are welcome! Please follow the [Contributing Guidelines](./.github/CONTRIBUTING.md) to contribute to this project.

<!-- xc-heading -->
## Development :toolbox:

Clone the repository and cd into the project directory:

```sh
git clone https://github.com/hasansezertasan/nur
cd nur
```

### `install`

Install the dependencies:

```sh
uv sync
```

### `style`

Run the style checks:

```sh
uv run --locked tox run -e style
```

### `ci`

Run the CI pipeline:

```sh
uv run --locked tox run
```

### `docs-build`

Build the documentation site:

```sh
uv run --locked tox run -e docs-build
```

### `docs-server`

Start the live-reloading docs server:

```sh
uv run --locked tox run -e docs-server
```

### `docs-linkcheck`

Check the documentation for broken links (also runs weekly in CI):

```sh
uv run --locked tox run -e docs-linkcheck
```

## Releasing

Versioning and releases are automated with [release-please](https://github.com/googleapis/release-please), driven by [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/) PR titles squash-merged into `main`. release-please maintains a release PR that bumps the version and `CHANGELOG.md`; merging it tags the release and publishes to PyPI. See the [Contributing Guidelines](./.github/CONTRIBUTING.md#releasing) for the commit conventions, and the [Repository setup](./docs/maintaining/setup.rst) guide for one-time configuration and optional post-launch integrations such as a social preview, downstream packaging, and Repology.

## Credits

This package was created with [Copier](https://github.com/copier-org/copier) and the [hasansezertasan/copier-pyproject](https://github.com/hasansezertasan/copier-pyproject) project template.

## License :scroll:

This project is licensed under the [MIT License](https://spdx.org/licenses/MIT.html).

## Changelog :memo:

For a detailed list of changes, see the [GitHub Releases](https://github.com/hasansezertasan/nur/releases). A `CHANGELOG.md` is generated automatically by release-please on each release.

<!-- Refs -->
[author]: https://github.com/hasansezertasan
[nur]: https://github.com/hasansezertasan/nur
