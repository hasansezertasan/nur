---
status: accepted
date: 2026-10-13
decision-makers: [hasansezertasan]
supersedes: 0001-provider-selection-criteria
---

# Completeness over popularity: every honestly supportable format, recorded in the repo

## Context and Problem Statement

[ADR 0001](0001-provider-selection-criteria.md) set the provider selection
rule: static discovery only, ranked by adoption × ecosystem fit.
[ADR 0004](0004-js-adjacent-task-runners.md) then retired the adoption gate and
the single-file scope for the JavaScript-adjacent runners and said it
"partially supersedes" 0001.
Two things are still unsettled:

1. **The rule is split across two records and still reads as popularity-led.**
   Open issues keep parking candidates on "niche", "overlaps `xc`" or
   "borderline fit" (#145, #146), and the declined set (#147–#150) cites
   adoption figures and a single-file scope that no longer exists.
2. **The evidence lives in issues, not in the repo.**
   Candidate research, evaluations and their results sit in GitHub issues
   (#78, #139–#150, #292, #294, #302) and one maintainer's notes.
   A clone of the repository does not carry the reasoning, so context is lost
   whenever an issue is closed, renamed or inaccessible.

`nur` exists to light up the terminal for **developers and for agents**.
An agent that runs `nur` in a project should find every task the project
declares, in whatever runner format it uses, not only the popular ones.
The aim is completeness, not popularity.

## Decision Drivers

- **Completeness** — a project's tasks should be discoverable whatever tool
  declares them.
- **Safety (unchanged)** — listing never executes project code.
- **Honesty (unchanged)** — what is listed is what was read; unsupported
  constructs stay visibly opaque instead of being guessed.
- **Durability** — every decision and the evidence behind it must be readable
  from the repository alone.

## Considered Options

1. **Keep ADR 0001 and amend it piecemeal** through later records, as ADR 0004
   did. Rejected: the effective rule stays spread over several documents.
2. **Replace the criteria with this record**, mark ADR 0001 as superseded, and
   move the candidate research into the repository.
3. **Accept every format regardless of safety.** Rejected: it would break the
   guarantee that makes `nur` safe to point at an untrusted checkout.

## Decision Outcome

Chosen option: **2**. This record supersedes ADR 0001. ADRs 0002, 0003 and
0004 remain valid and now read against this record.

### The gates

A format becomes a provider when it passes **all three** gates. Popularity is
not a gate.

1. **Safe listing (hard rule, carried over from ADR 0001).** Discovery reads
   files; it never runs the project's task code, never evaluates
   project-controlled expressions and never shells out to a runner to
   enumerate (`make -pRrq`, `gradle tasks`, `nox -l`, `invoke --list`,
   `rake -T`, `argc --argc-eval`, `grunt --help`, `gulp --tasks` are all
   forbidden for listing).
2. **A faithful run command.** The task can be run *locally and now* by a
   delegated invocation (`<runner> <name>`) that does what the source file
   says. If the delegated command cannot reproduce the declared task, the
   format does not pass.
3. **An honest subset.** Task names can be read without evaluation. The
   provider may list a safe subset, must document what it leaves opaque, and
   must present results as *syntactic task candidates* (the contract from
   ADRs 0002 and 0003). Completeness yields to gate 1, never the reverse.

### Scope

Discovery is **rooted at the current directory** (ADR 0004). A provider may read
files under `cwd` that the source file points to (workspace globs, imports it
can resolve statically) and must never escape `cwd`. The "single well-known
file" clause of ADR 0001 is retired.

### What is no longer a reason to decline or defer

Adoption or census figures, niche ecosystems, overlap with another provider,
and "borderline fit" are **not** grounds to decline or park a format. Figures
may still be recorded as information. If a format passes the three gates it is
accepted and queued for implementation; ordering is a scheduling matter, not a
verdict. A gate that is merely unresolved leaves the format *pending*, not
declined.

### What still declines a format

Only a failed gate. Every declined record must name which gate fails, say what
would have to change for it to pass, and list its **revisit trigger**. A decline
is a recorded fact about the format, not a priority call, and it is reopened
when the fact changes.

### Re-triage of the open candidates

| Format | Prior state | Gates | Decision |
| ------ | ----------- | ----- | -------- |
| Procfile (#145) | parked: "borderline, not a task runner" | 1 yes, 3 yes, 2 unresolved until a runner is chosen | **Pending.** Becomes accepted once one delegated invocation is chosen and validated (see [candidates](../providers/candidates.md)). Not declined. |
| JBang catalog (#146) | parked: "niche" | all pass | **Accepted.** |
| argc (#146) | parked: "niche" | all pass (comment scan, never `--argc-eval`) | **Accepted.** |
| GitHub Actions (#147) | declined: not local, multi-file | gate 2 fails; the multi-file ground is retired by the rooted scope | **Declined on gate 2 alone.** |
| Gradle / Maven (#148) | declined | Gradle: gate 3 (any static subset is misleading) and gate 1 for real enumeration; Maven: gate 3 (no project-specific task list) | **Declined.** |
| CMake (#149) | declined | gates 2 and 3 fail (configure/build two-step; names are runtime products) | **Declined.** |
| cargo-xtask (#150) | declined | no declarative file; gate 3 fails | **Declined.** |

The two JBang/argc formats were filed together in #146 but are independent
providers and are tracked separately.

### Where the record lives

The repository is the source of truth. Issues are working trackers and may be
closed once their content is in the repo.

- [`docs/providers/`](../providers/index.md) holds the **provider register**:
  one table of every format considered and its state, the evaluation template,
  and the full evaluation of each candidate, implemented provider, declined
  format and research effort, including measured results.
- Each new candidate gets a register entry **before** an implementation issue is
  opened. Each implemented provider keeps its evaluation after it ships.
- Decisions that change the rules, or adopt a dependency, are ADRs.
  Evaluations and their results are register pages.
- ADR 0001 is not rewritten; only its `status` changes to point here.

## Consequences

- Good: one document states the rule; the question "should `nur` support X?"
  becomes "does X pass the three gates?".
- Good: a fresh clone carries every candidate, evaluation and result, so an agent
  or a new contributor never depends on closed issues.
- Good: the niche formats parked on popularity become scheduled work (JBang,
  argc) or pending a concrete decision (Procfile).
- Bad / accepted: more providers to maintain, and each new format widens the
  parsing surface. The bounded contracts of ADRs 0002 and 0003 are the
  mitigation.
- Bad / accepted: the register must be kept current. A provider PR updates its
  register entry in the same change.
- Follow-up: open implementation issues for JBang and argc, and settle the Procfile runner; once the
  register is merged, close #78 and #147–#150 with a pointer to it (their
  content now lives in the repo).

## More Information

- Superseded: [ADR 0001](0001-provider-selection-criteria.md).
- Builds on: ADR 0002 and 0003 (bounded static discovery), ADR 0004 (adoption
  gate and single-file scope retired).
- Register: [Provider register](../providers/index.md).
- Revisit if: a format that passes the gates can only be supported by a
  dependency that cannot be packaged for the supported platforms.
- Template: [MADR 4.0.0](https://adr.github.io/madr/).
