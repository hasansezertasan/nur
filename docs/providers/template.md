# Evaluation template

Copy this for every new format. Every field is required; write "n/a" with a
reason instead of leaving one out. Evaluate against the three gates in
[ADR 0005](../adr/0005-completeness-over-popularity.md).

```markdown
## <Format name>

- **Format:** what it is, one line.
- **Source file(s):** the file(s) read. Note any glob or pointer files.
- **Docs / spec:** primary-source links.
- **State:** Accepted | Implemented | Declined.

### How it is used
A minimal example of the file and how the runner invokes a task.

### Gate check
1. **Safe listing.** Can names be read without running project code or
   evaluating project-controlled expressions? Which native listing command
   must *not* be used?
2. **Faithful run command.** What delegated invocation runs the task locally
   and does what the file says? What does it need beforehand?
3. **Honest subset.** What is read, and what is left opaque?

### Task-model mapping
`prefix`, `name`, `argv_base`, `description`, `definition`, `source_file`,
`passthrough_prefix`.

### Parsing edge cases
Verified against primary docs. Cite what was confirmed and flag what was not.

### Deferred
What a first cut deliberately omits.

### Adoption (information only)
Figures with their source, or "no figure known". Never a reason to accept,
decline or order work.

### Decision
Accepted / declined, which gate decides it, and for a decline the
**revisit trigger**.
```
