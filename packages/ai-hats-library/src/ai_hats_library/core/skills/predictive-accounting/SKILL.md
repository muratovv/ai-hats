---
name: predictive-accounting
description: For shrink/refactor/optimize/cleanup tasks — present baseline + predicted delta + dependency cost BEFORE implementation, not after
license: MIT
---

# Predictive Accounting

For tasks in the **shrink / refactor / extract / optimize / cleanup** family —
show the user, **before any code changes**:

1. The current baseline of the metric being improved (tokens, lines, time, allocations).
2. The predicted delta after the proposed changes.
3. The dependency cost: does the win materialize immediately, or only after another task lands?
4. The rough work cost (number of files / commits / phases).

Ask for confirmation **before** starting implementation. Do not arrive at
1-2 commits of "structural prep" that, at that moment, makes the metric
**worse** — and then surface the honest accounting post-hoc.

## When to Use

- Task description verbs: **shrink / trim / refactor / extract / optimize / cleanup / consolidate / split**.
- Task mentions a **target saving** (e.g., "~150 lines", "drop ~500 tokens") with or without precise numbers.
- Plan touches large refactors of existing components where short-term cost is non-trivial.

## Checklist

### Before writing the plan

1. **Measure the current baseline.** Run the actual count (tokens, lines, prompt size). Do not estimate.
2. **Predict the delta from each proposed change.** For each change, estimate:
   - Short-term delta (right after the commit lands).
   - Long-term delta (after dependent tasks land, if any).
3. **List dependencies.** If a refactor's full saving is gated on another unmerged task — name it (`HATS-XXX`) and quantify the gated portion separately.
4. **Estimate work.** Number of files, commits, phases. Hours-of-attention if relevant.

### Present accounting to the user before execute

Show a table:

| Change                      | Short-term delta | Long-term delta | Dependency        | Work cost |
| --------------------------- | ---------------- | --------------- | ----------------- | --------- |
| Inline skill X into trait Y | +1086 tokens     | -458 tokens     | requires HATS-307 | 1 commit  |
| Collapse traits A+B+C → D   | -205 tokens      | -205 tokens     | none              | 1 commit  |
| ...                         | ...              | ...             | ...               | ...       |

Wait for explicit confirmation. If short-term is **negative**, the user
should be able to choose: "skip", "do only the dependency-free wins",
"proceed knowing temporary regression".

### After execute

5. Re-measure. Compare to the predicted delta. If off by >20% — log in the work_log; useful for future predictive accuracy.

## Worked Example

**HATS-309 (2026-05-13) — "shrink ~120-150 lines".**

The agent (without this skill) made:

- **Commit 1**: rules-pass refactor (inline skill bodies into traits).
- **Commit 2**: traits-pass refactor (collapse adjacent traits).

Then presented honest accounting:

- **Short-term**: +1086 tokens (new skill bodies inlined permanently grow prompts).
- **Long-term**: -458 tokens, **only after** HATS-307 lands (which removes the now-redundant skill files).

User reverted both commits, kept only the trait-collapse work which delivered a clean **-205 tokens immediately**, no dependency.

The waste: two days of design + two commits + one revert. Filed as **HYP-013**
("predictive accounting prevents sunk-cost commits in shrink tasks").

## Anti-Patterns

- **Starting refactor commits before measuring baseline.**
- **"It'll be cleaner anyway" justification** without delta numbers.
- **Bundling all changes into one PR** when some have no dependencies — give the user the option to take the wins-now subset.
- **Post-hoc accounting** ("here's what it cost") after the user is already psychologically invested in your work.
- **Hiding gated savings** — "this saves X" without "after HATS-Y lands".

## Completion

The task passes the accounting gate when:

- Baseline numbers are recorded in `plan.md` or work_log.
- Predicted delta is presented before execute.
- User has explicitly confirmed (proceed / partial / abort).
- Post-execute delta is compared to prediction; major drift logged.

## See also

- `design-minimalism` — same upstream principle (don't add primitives without justification), applied at design phase.
- `scope-guard` — implementation-stage discipline; this skill is upstream.
- `request-supervisor` — when negative short-term delta means user-decision territory.
