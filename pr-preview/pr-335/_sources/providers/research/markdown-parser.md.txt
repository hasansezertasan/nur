# Markdown parser evaluation (mask and xc)

Cross-cutting research behind issues #292 (broader tree-sitter adoption), #294
(`just`) and #302 (a Markdown parser for `mask` and `xc`). The results are kept
here so they survive the issues. **Status: evaluation complete, decision
pending.** A decision that adopts a dependency becomes an ADR (see "Decision
pending").

## Question

Could a parser replace the handwritten Markdown scanner that the `mask` and `xc`
providers share (about 1,100 lines in `_markdown.py` and `_inline.py`,
about 1,450 with the parsing half of `mask.py`)?

Correctness here means matching each *runner*, not CommonMark. `mask` pins
pulldown-cmark 0.5, and the scanner reproduces its pre-spec quirks on purpose.
`xc` layers its own rules over its parser. A spec-correct parser can therefore be
less faithful than the current scanner. tree-sitter-markdown explicitly
discourages use where correctness matters, so a migration needs evidence.

## Method

- **Ground truth from the runners.** `mask` 0.11.7 and `xc` 0.9.0 built from
  source; `mask --introspect` and `xc -short` as the reference. Neither runs task
  scripts.
- **One task builder for every parser.** It ports mask's `parse()` event loop and
  reuses nur's tree and CLI resolution, so differences come from the parser alone.
- **Two inputs:** every Markdown text `nur`'s mask and xc tests feed the parsers
  (359 mask and 35 xc inputs), and real files from GitHub fetched at pinned
  commits (100 `maskfile.md` and 80 xc READMEs).
- **A match** means the same task names, scripts and descriptions as the runner.
  Whitespace in descriptions is ignored.

### Candidates

| Candidate | Version | Notes |
| --------- | ------- | ----- |
| nur handwritten scanner | repository | baseline |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | 4.2.0 | pure Python; already installed through `rich` and `textual` |
| [pyromark](https://github.com/monosans/pyromark) | 0.9.14 | wraps pulldown-cmark 0.13.4 (mask uses 0.5.3) |
| [mistletoe](https://github.com/miyuchina/mistletoe) | 1.6.0 | |
| [tree-sitter-markdown](https://github.com/tree-sitter-grammars/tree-sitter-markdown) | 0.5.1 | latest on PyPI |

## Results: mask

Full matches against the real `mask`:

| | Real corpus (100) | nur test inputs (359) |
| - | ----------------- | --------------------- |
| nur scanner | 83 | 350 |
| pyromark | 100 | 346 |
| markdown-it-py | 100 | 330 |
| tree-sitter-markdown | 100* | 324* |
| mistletoe | 100 | 287 |

\* With nur-side handling for what a syntax tree leaves to the consumer
(resolving `[x]` shortcut links against definitions, stripping fence indentation
from script lines) and a workaround for one upstream bug. Without these:
88 and 299.

- **The scanner's misses on the real corpus.** Task names are always correct. The
  17 misses come from three things: tabs in scripts rewritten as spaces (9 files),
  trailing blank lines in scripts dropped, and descriptions that keep raw Markdown
  where `mask` shows the text it displays.
- **The parsers' misses on the test inputs** are adversarial cases that pin
  pulldown-cmark 0.5 quirks: lone CR line endings, Unicode spaces inside HTML
  tags, control characters in link destinations, reference-label limits.
- **Code a parser would replace:** about 1,450 lines shrinking to an event loop of
  about 80.

## Results: xc

- `xc` has no Markdown parser; its parser is about 340 lines of handwritten,
  line-based Go (`parser/parsemd`).
- `nur` matches the real `xc` on 79 of 80 real READMEs, but on only 23 of its own
  35 test inputs. The differences are `~~~` fences, indented fences, nested
  fences, unclosed blocks and the marker's priority, which `nur` reads the
  CommonMark way and `xc` does not.
- **A Markdown parser would not bring `nur` closer to `xc`.** A faithful port of
  `xc`'s line rules would.

## Upstream work on tree-sitter-markdown

tree-sitter-markdown matches the other parsers on the real corpus and `nur`
already ships the tree-sitter runtime, so its gaps were checked for upstream
fixes. Issues filed, each reproduced on `split_parser` (a0a00f8) and the 0.5.1
wheel (all under `tree-sitter-grammars/tree-sitter-markdown`):

| # | Defect |
| - | ------ |
| 266 | a closing fence on the last line without a line ending becomes code content |
| 267 | an ATX heading's content includes its closing `##` |
| 268 | `<!-->` and `<!--->` do not end the HTML block, so the rest of the file is swallowed |
| 269 | a link definition labelled `[x]` or `[X]` is read as a task-list marker |
| 270 | publish 0.5.2 and 0.5.3 to PyPI, which is still at 0.5.1 with `tree-sitter~=0.23` |

Pull requests, both with passing CI: #271 fixes #266 (grammar and scanner), #272
fixes #268 (scanner only).

**Upstream health (checked 2026-10-11).** One active maintainer; last non-bot
merge 2026-05-06; last release v0.5.3 on 2026-02-26, not on PyPI; 17 open PRs,
most unreviewed. The maintainer closed an overlapping EOF fix (#262) on
2026-09-24, stating a policy of not merging or reviewing LLM-generated PRs
touching critical code. Expect #271 and #272 to be closed or left unreviewed.
Without a PyPI release `nur` cannot depend on any fix.

## Alternative checked: tree-sitter-language-pack

Checked 2026-10-13 (v1.22.1, MIT, abi3, returns py-tree-sitter `Language`
objects). The wheel contains no grammars: the first `get_language("markdown")`
downloads a roughly 26 MB per-platform bundle from its GitHub releases into a
cache keyed by pack version, and the pack releases almost daily. It pins
tree-sitter-markdown upstream HEAD a0a00f8 (2026-07-19), which still has issues 266 and 268, and carries its own `patches/markdown/`
(a serialize heap-overflow fix).

Harness results match PyPI 0.5.1: corpus mask 100/100 and xc 80/80; fixtures
mask 321/359 (versus 324). **Rejected for `nur`: it needs network at runtime.**

## Other tree-sitter adoptions (#292)

- **make:** adopted. See [ADR 0003](../../adr/0003-bounded-make-discovery.md).
- **rake:** adopted. See [ADR 0002](../../adr/0002-bounded-rake-discovery.md).
- **Grunt, Gulp:** adopted with `tree-sitter-javascript`
  ([ADR 0004](../../adr/0004-js-adjacent-task-runners.md)).
- **just (#294), open:** replace recipe-header and attribute regexes and manual
  multiline-literal tracking (whose delimiter tracking is described as naive)
  with [tree-sitter-just](https://github.com/casey/tree-sitter-just). Acceptance
  includes: no phantom tasks from text inside strings, backticks or recipe bodies;
  literal `doc` attributes taking precedence over adjacent comments; literal
  local aliases producing the correct invocation; documented private and platform
  attribute behavior; malformed-input tests; packaging checks for supported
  platforms. Imports and external modules stay opaque.
- **Retain existing parsers:** nox and invoke (stdlib `ast`; a parser swap alone
  would not simplify semantic analysis), and the JSON, TOML and YAML providers.

## Decision pending

Re-run the comparison once upstream responds (or decides not to), then record the
choice as a new ADR (the number is the next free one; ADR 0004 is taken):
**tree-sitter-markdown, markdown-it-py, or keeping the scanner.** Keeping the
scanner is an acceptable outcome. Whatever is chosen, follow-up work is worth
filing for: `mask` tab and trailing-blank-line handling in scripts, raw-Markdown
descriptions, and `xc` fidelity (a faithful port of its line rules).

The #302 evaluation comment named ADR 0004 for this decision; that number belongs to the
JS-adjacent runners record.
