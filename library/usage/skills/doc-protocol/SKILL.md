---
name: doc-protocol
description: Plan-stage style forks + scope triage + pre-commit artifact verification for documentation tasks
---
# Doc Protocol

Three coordinated checks for any task that **changes documentation text**
(`docs/*.md`, `README.md`, `CONTRIBUTING.md`, `how-to-*.md`, `ARCHITECTURE.md`,
`reflect.md`, glossary, etc.):

1. **Plan-stage style forks** — surface style conventions as explicit forks before writing.
2. **Plan-stage breadth triage** — when task enumerates many items, propose curated subset.
3. **Pre-commit artifact verification** — grep every named code-artifact in the doc to catch phantom references.

Each check fires at a different lifecycle stage but shares one audience
(the user reviewing the doc PR) and one failure mode (extra review rounds).

## When to Use

- Task changes any `docs/*.md`, `README.md`, `CONTRIBUTING.md`.
- Task lifts structure from a "precedent doc" ("I'll match the style from X").
- Task description enumerates ≥6 items to document (components, sections, recipes).
- Task adds or renames names of code-side artifacts (skills/roles/paths/CLI).

---

## Section 1: Plan-Stage Style Forks

Doc tasks accumulate review rounds when style conventions are settled silently.
**Surface them as explicit forks at plan stage** — saves 2–3 review iterations.

### Fork checklist (include in `plan.md` under `## Style conventions`)

1. **Reference format** — inline `[text](url)` / inline link with title / numbered footnote `[N]` / markdown reference-style.
2. **Voice** — imperative ("Run …") / descriptive ("The runner runs …").
3. **In-snippet code comments** — terse (just-the-code) / pedagogical / cross-link tags / none.
4. **Cross-link policy** — link on first mention only / re-link on every occurrence.
5. **Terminology source-of-truth** — name a single source (glossary doc, code module) and pin all rename-able names to it before writing.
6. **Code blocks** — agent-runs-this / user-runs-this / mixed (label each).

### Precedent-doc trap

When lifting structure from an existing doc ("I'll match the numbered-refs
style from `how-to-feedback-loop.md`"):

- **Name the precedent in the plan-stage fork.**
- Ask: "keep this style or simplify?"
- Precedent docs are not automatically canonical. They may have landed without
  full user approval, and may drift later.

**Worked example.** HATS-358: lifted dual numbered-refs format (`**[N]**` visible
section + `[N]: url` defs) from `how-to-feedback-loop.md`. User dropped the
link-ref defs on first review; HATS-360 later converged the convention to
visible-section-only across all docs.

Skip section 1 only for trivial single-paragraph doc edits.

---

## Section 2: Plan-Stage Breadth Triage

When a doc task description enumerates **≥6 items** to document (components,
sections, recipes, fixtures), the enumeration is a **starting set, not a contract**.
The user wants a high-signal subset, not a full catalog.

### Triage procedure

At plan stage, do NOT plan to enumerate every item. Instead:

1. **Identify the high-signal subset** — items that show up by name in user-facing prose, are likely to be cross-doc cited, or define an interface most readers actually meet.
2. **Present a keep / drop / merge triage** via `AskUserQuestion` or as an explicit plan-stage fork.
3. **Defaults:**
   - Keep **≤5** items.
   - **Merge clusters** that share an audience (e.g., "judge / judge-for-role / auditor-for-role" → one entry for the reflection-role cluster).
   - **Drop low-surface items** (engine-internal plumbing, names referenced only from code).

Treat the task-description enumeration as **research notes** (which items
exist), not as the deliverable shape.

### Worked example

**HATS-364 (glossary extend, 2026-05-17).** Task listed 37 components from
`library/core/` (7 roles + 6 traits + 5 rules + 19 skills) with a one-line
purpose each. Initial plan: document all 37.

User trimmed to **5 entries** (3 key roles + 2 key traits), dropped Rules and
Skills sections entirely. Rationale: glossary entries pay a reader-attention
cost; only items that show up in cross-doc prose earn a slot. The detail
lives in `ai-hats list ...` and per-component files.

Result with triage: 4 hours saved + tighter doc.

---

## Section 3: Pre-Commit Artifact Verification

Doc text accumulates phantom references: skill/role/path/CLI names that no
longer match the code (renames, removed components, drift). Catch them before
the user does.

### Verification procedure (before commit)

When the doc names code-side artifacts (skills, roles, schemas, file paths,
CLI commands), run a `grep` pass for each:

```bash
# Extract artifact-name candidates from the doc
grep -oE "[a-z][a-z_-]*-[a-z][a-z_-]*-(skill|role)|library/(core|usage)/(skills|roles|traits|rules)/[a-z_-]+" docs/<file>.md | sort -u

# For each candidate, verify it exists in source
for name in <list>; do
    test -e "library/core/skills/$name" \
        || test -e "library/usage/skills/$name" \
        || test -e "library/core/roles/$name" \
        || test -e "library/usage/roles/$name" \
        || echo "MISSING: $name"
done
```

Also grep CLI commands referenced in prose against actual CLI:

```bash
ai-hats --help | grep <subcommand>
ai-hats <subcommand> --help | grep <flag>
```

Surface any phantom references **before commit**. Update the doc, file a
backlog item, or delete the reference.

### Worked example

**HATS-354 sync-pass on `how-to-feedback-loop.md`.** Doc referenced a
`hypothesis-workflow` skill that does NOT exist in `library/`. Phantom from
pre-HATS-252 era — predecessor never grep-verified. Caught at round-2 user
review. Same root cause as a stale `reflect all` → `judge` role mention
elsewhere in the same doc.

Applies to **translation tasks** (`feedback-doc-style-upfront`) AND
**drift-fix tasks** alike — verification is orthogonal to text content.

---

## Anti-Patterns

- **Settling style silently** — landing the PR and discovering 6 style questions only in review.
- **Enumerating all ≥6 items** because they were listed in the task description — enumeration ≠ contract.
- **Skipping artifact verification** on a doc that mentions code-side names — phantom refs accumulate.
- **Lifting precedent doc structure** without asking "keep this or simplify?" — precedent drift bypasses user approval.
- **Bundling style decisions into review feedback** — pay 2-3 rounds upstream of writing.

## Completion

A doc task passes this protocol when:

- `plan.md` has a `## Style conventions` section with the 6 forks resolved.
- If enumeration ≥6: triage was performed and ≤5 items remain (or user explicitly waived).
- Every named code artifact in the doc passes a `grep` check against source.
- Numbered-refs convention from CONTRIBUTING followed (if cross-doc/cross-file/fixture links).

## See also

- `design-minimalism` — same upstream principle (curate, don't enumerate), applied at design phase.
- `scope-guard` — implementation-stage scope discipline.
- `CONTRIBUTING.md#documentation-references` — numbered-refs format spec.
- `docs/glossary.md` — terminology source-of-truth.
