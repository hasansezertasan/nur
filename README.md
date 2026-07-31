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

Run `nur` in a project and it discovers tasks across npm (`package.json`),
`Makefile`, PDM/poe (`pyproject.toml`), `justfile`, and `Taskfile.yml`, then
lets you run them from a TUI picker or directly from the command line.
Discovery is limited to the current directory.

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

nur is not on PyPI yet. Install the latest from source:

```console
uv tool install git+https://github.com/hasansezertasan/nur
```

Or, from a clone:

```console
uv tool install .
```

Once published, `nur` will also be installable from PyPI (`pip install nur` /
`uv tool install nur`), Homebrew, and Scoop.

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

### Debugging

Debug nur in VS Code using the provided launch configurations:

- **Current File**: Debug the currently open Python file.
- **Tests**: Debug pytest runs.
- **Attach**: Attach to a running process (with debugpy).
- **CLI / TUI**: Debug the `nur` command-line and terminal-UI entry points.

Select a configuration from the Run and Debug panel in VS Code.

## Support :heart:

If you have any questions or need help, feel free to open an issue on the [GitHub repository][nur].

## Motivation

Every project speaks a different task dialect — `make test`, `npm run test`,
`just test`, `task test`, `pdm run test`, `poe test`. nur gives you one command
that discovers whatever a project already uses and runs it, with no config and
no need to remember which runner lives where. Discovery is pure text/JSON/TOML
parsing, so listing tasks never executes anything (no `make -pRrq` side effects).

## Features

- **Zero-config discovery** across six providers: `make`, `npm`, `just`,
  `task` (Taskfile), `pdm`, and `poe`.
- **CLI Application**: run any discovered task by name or qualified `prefix:name`, with `--` passthrough to the underlying runner.
- **TUI Application**: interactive three-pane task picker built with Textual.
- **Safe by default**: discovery parses files; it never shells out to a runner just to list tasks.
- **Type Safety**: full type hints checked by mypy, basedpyright, ty, pyrefly, and zuban.
- **Code Quality**: comprehensive linting and formatting with ruff.
- **Testing**: pytest with coverage reporting and parallel execution.
- **Documentation**: Sphinx documentation with the Shibuya theme, GitHub Pages deployment, and live per-PR documentation previews.
- **CI/CD**: automated testing, building, and publishing across multiple platforms.
- **Security**: CodeQL, OpenSSF Scorecard, dependency review, secret scanning (gitleaks), dependency auditing (pip-audit), GitHub Actions static analysis (zizmor), hardened least-privilege workflows, and a CycloneDX SBOM attached to every release.
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

Versioning and releases are automated with [release-please](https://github.com/googleapis/release-please), driven by [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/) PR titles squash-merged into `main`. release-please maintains a release PR that bumps the version and `CHANGELOG.md`; merging it tags the release and publishes to PyPI. See the [Contributing Guidelines](./.github/CONTRIBUTING.md#releasing) for the commit conventions and the one-time [Repository setup](./.github/CONTRIBUTING.md#repository-setup-one-time) (squash-merge settings, Actions permissions, release immutability, and PyPI trusted publishing).

## Credits

This package was created with [Copier](https://github.com/copier-org/copier) and the [hasansezertasan/copier-pyproject](https://github.com/hasansezertasan/copier-pyproject) project template.

## License :scroll:

This project is licensed under the [MIT License](https://spdx.org/licenses/MIT.html).

## Changelog :memo:

For a detailed list of changes, see the [GitHub Releases](https://github.com/hasansezertasan/nur/releases). A `CHANGELOG.md` is generated automatically by release-please on each release.

<!-- Refs -->
[author]: https://github.com/hasansezertasan
[nur]: https://github.com/hasansezertasan/nur
