---
name: Bug report
about: Report a bug in `nur`
title: 'Bug: '
labels: bug
assignees: 'hasansezertasan'
---
## Bug Description

<!--
This issue tracker is a tool to address bugs in nur itself.
Please use GitHub Discussions for questions about your own project's tasks.

Replace this comment with a clear description of what the bug is.
-->

## How to Reproduce

<!--
Provide a minimal reproducible example that developers can run to investigate.
See https://stackoverflow.com/help/minimal-reproducible-example for guidance.

Because nur discovers tasks from the files in a directory, the most useful
report includes the source file(s) it was run against and the exact command.

For example, given a `package.json`:

```json
{ "scripts": { "build": "tsc", "test": "vitest" } }
```

running:

```shell
nur list
nur test
```

Include the full output or traceback if there was an exception. For example:

```console
$ nur build
nur: runner 'tsc' is not installed (needed to run npm:build).
```

Tell us which task provider is involved (make, npm, just, task, pdm, poe, mise,
xc) and whether the problem is in discovery, the CLI, or the TUI.
-->

## Expected Behavior

<!-- Describe the behavior you expected but did not get. -->

## Environment

<!--
Please complete the following information:

- nur version (`nur --version`): [e.g. 0.1.0]
- Python version (`python --version`): [e.g. 3.14.0]
- OS / terminal: [e.g. macOS 15, iTerm2]
- Task provider involved: [make | npm | just | task | pdm | poe | mise | xc]
- Surface: [discovery | CLI | TUI]
-->

### Additional Context

<!-- Add any other context about the problem here. -->
