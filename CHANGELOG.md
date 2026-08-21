# Changelog

## [0.5.0](https://github.com/hasansezertasan/nur/compare/v0.4.0...v0.5.0) (2026-08-21)


### 🚀 Features

* **templates:** add provider issue template for task-discovery formats ([#137](https://github.com/hasansezertasan/nur/issues/137)) ([e02af44](https://github.com/hasansezertasan/nur/commit/e02af44e23a2a19f21eaf417b8a01fa256843380))

## [0.4.0](https://github.com/hasansezertasan/nur/compare/v0.3.0...v0.4.0) (2026-08-13)


### 🚀 Features

* add moon (moonrepo) provider for task discovery ([#121](https://github.com/hasansezertasan/nur/issues/121)) ([9fb4302](https://github.com/hasansezertasan/nur/commit/9fb4302969e3c2f0d4124ad537bcf51e719f2437))


### ♻️ Refactoring

* **just:** parse justfile statically instead of shelling out ([#122](https://github.com/hasansezertasan/nur/issues/122)) ([0362df9](https://github.com/hasansezertasan/nur/commit/0362df9ff0e4f100d0e537c0da6fc50d5c46a531))

## [0.3.0](https://github.com/hasansezertasan/nur/compare/v0.2.1...v0.3.0) (2026-08-12)


### 🚀 Features

* add cargo-make provider for task discovery ([#92](https://github.com/hasansezertasan/nur/issues/92)) ([6ccd6b1](https://github.com/hasansezertasan/nur/commit/6ccd6b12884055cf8e0698907eaef2e40d397dbe))
* add composer provider for task discovery ([#109](https://github.com/hasansezertasan/nur/issues/109)) ([c0882b4](https://github.com/hasansezertasan/nur/commit/c0882b49dcd29c520151b623931677e41aeae02c))
* add deno provider for task discovery ([#93](https://github.com/hasansezertasan/nur/issues/93)) ([ee096f7](https://github.com/hasansezertasan/nur/commit/ee096f76066a2c72e5a1428ae4c0d8e48f2fd283))


### 🐛 Bug Fixes

* **renovate:** resolve copier template git-tags lookup failure ([#85](https://github.com/hasansezertasan/nur/issues/85)) ([b4eb59f](https://github.com/hasansezertasan/nur/commit/b4eb59f758cf93f6a27aa7bb020613ad80bd325d))


### ♻️ Refactoring

* adopt the template's src/nur/core/ package layout ([#107](https://github.com/hasansezertasan/nur/issues/107)) ([3ff5afa](https://github.com/hasansezertasan/nur/commit/3ff5afa29f27c9096c5afe8e17a021d3b869418f))


### 📝 Documentation

* add ADR-0001 provider selection criteria ([#90](https://github.com/hasansezertasan/nur/issues/90)) ([0c76d8f](https://github.com/hasansezertasan/nur/commit/0c76d8f3a0a1ceb7a948d3d0f7de31f8f5ef9f0b))

## [0.2.1](https://github.com/hasansezertasan/nur/compare/v0.2.0...v0.2.1) (2026-08-06)


### 🐛 Bug Fixes

* correct stale `nur-tui` references and pre-PyPI install docs ([#59](https://github.com/hasansezertasan/nur/issues/59)) ([0381e10](https://github.com/hasansezertasan/nur/commit/0381e10763084c07a988940606117bff3061b43b))
* migrate suppression comments to ruff 0.16 structured syntax ([#66](https://github.com/hasansezertasan/nur/issues/66)) ([0b7469d](https://github.com/hasansezertasan/nur/commit/0b7469d6915e34b152e11191b724ef2df26c7b18))


### 🛠 Build

* **deps:** include all dependency groups in dev ([#60](https://github.com/hasansezertasan/nur/issues/60)) ([dd9f91b](https://github.com/hasansezertasan/nur/commit/dd9f91ba42776d11558d181b0f00113549259382))

## [0.2.0](https://github.com/hasansezertasan/nur/compare/v0.1.0...v0.2.0) (2026-08-05)


### 🚀 Features

* **providers:** add mise provider for task discovery ([#33](https://github.com/hasansezertasan/nur/issues/33)) ([adc7b66](https://github.com/hasansezertasan/nur/commit/adc7b66be6bb4d7efe9a1a222c83102ea145adf7))
* **providers:** add xc provider for task discovery ([#37](https://github.com/hasansezertasan/nur/issues/37)) ([e701f97](https://github.com/hasansezertasan/nur/commit/e701f97d4b9cbc43d5095f38f1761a44409c31b8))
* start the TUI instantly and run task discovery in the background ([#17](https://github.com/hasansezertasan/nur/issues/17)) ([484bf60](https://github.com/hasansezertasan/nur/commit/484bf60ea25aae8a3a73fd1a3eb0fae41a1fb37e))
* support `python -m nur` as a console-script alias ([#9](https://github.com/hasansezertasan/nur/issues/9)) ([2809f43](https://github.com/hasansezertasan/nur/commit/2809f435ea64abcc9978e08f535e6a3b55436abb))

## 0.1.0 (2026-07-31)


### 🚀 Features

* scaffold nur from copier-pyproject with Typer CLI and Textual TUI ([6046e58](https://github.com/hasansezertasan/nur/commit/6046e58a1a0c2b222d8a479977c87e526229193c))


### 🐛 Bug Fixes

* exclude release-please CHANGELOG.md from markdownlint ([#5](https://github.com/hasansezertasan/nur/issues/5)) ([5d92138](https://github.com/hasansezertasan/nur/commit/5d92138b9215f9ce21e71bcc442f95dfcb3b87c9))
* repair CI and release-please failures from initial scaffold ([#3](https://github.com/hasansezertasan/nur/issues/3)) ([a37b189](https://github.com/hasansezertasan/nur/commit/a37b189112438ed33ada574cd72835dd9dc6753f))


### 🧪 Tests

* **tui:** harden flaky interrupt test with wall-clock wait ([#6](https://github.com/hasansezertasan/nur/issues/6)) ([c8fc893](https://github.com/hasansezertasan/nur/commit/c8fc893cbb548fe1b034997ed9f406677c0495c3))
