---
name: doc-protocol
description: Use when a task changes `docs/*.md`, `README.md` or `CONTRIBUTING.md`, lifts structure from a precedent doc, enumerates six or more items to document, or adds or renames a code-side name (skill, role, path, CLI).
license: MIT
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

Scope is *prose docs a human reviews* — `docs/*.md`, `README`, `CONTRIBUTING`,
how-to / architecture pages. **Not** code comments or docstrings (those ride
along with the code change), and **not** recording an architecture decision —
that's **adr-manager**. Fires when:

- Task changes any `docs/*.md`, `README.md`, `CONTRIBUTING.md`.
- Task lifts structure from a "precedent doc" ("I'll match the style from X").
- Task description enumerates ≥6 items to document (components, sections, recipes).
- Task adds or renames names of code-side artifacts (skills/roles/paths/CLI).

---

## Section 1: Plan-Stage Style Forks

Doc tasks accumulate review rounds when style conventions are settled silently.
**Surface them as explicit forks at plan stage** — saves 2–3 review iterations.

### Fork checklist (include in `plan.md` under `## Style conventions`)

Any style convention that would earn a review comment if you guessed wrong is a
plan-stage fork. The six below are the ones that recur — surface others as you
meet them (line-wrap width, heading numbering, table-vs-list are common).

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

**Worked example.** A doc lifted the dual numbered-refs format (`**[N]**` visible
section + `[N]: url` defs) from `how-to-feedback-loop.md`. User dropped the
link-ref defs on first review; a later pass converged the convention to
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
   - **Merge clusters** that share an audience (e.g., "judge / role-judge / role-auditor" → one entry for the reflection-role cluster).
   - **Drop low-surface items** (engine-internal plumbing, names referenced only from code).

Treat the task-description enumeration as **research notes** (which items
exist), not as the deliverable shape.

### Worked example

**A glossary-extend task.** It listed 37 components from
`packages/ai-hats-library/src/ai_hats_library/core/` (7 roles + 6 traits + 5 rules + 19 skills) with a one-line
purpose each. Initial plan: document all 37.

User trimmed to **5 entries** (3 key roles + 2 key traits), dropped Rules and
Skills sections entirely. Rationale: glossary entries pay a reader-attention
cost; only items that show up in cross-doc prose earn a slot. The detail
lives in `ai-hats list ...` and per-component files.

Result with triage: 4 hours saved + tighter doc.

---

## Section 3: Pre-Commit Claim Verification

Doc text accumulates claims that were true once — a renamed skill, a moved
path, a glob that stopped matching, a count that drifted. The invariant and the
four kinds of checkable claim are rule `rule_verify_authored_claims`; this
section is the doc-side recipe for applying it.

### Verification procedure (before commit)

Unroll every claim the doc makes about code-side artifacts. Names are the
common case, not the whole job: a glob, an "every"/"always", and a count rot
the same way and are just as invisible to the test suite.

```bash
# Resolve the library from the checkout you are IN — never hard-code the root,
# it has moved once already, and a literal path silently measures main.
LIB=$(python3 -c 'import ai_hats_library, pathlib; print(pathlib.Path(ai_hats_library.__file__).parent)')

# Names: does each one resolve, here, now?
for name in <candidates>; do
    find "$LIB" -maxdepth 3 -type d -name "$name" | grep -q . || echo "MISSING: $name"
done

# Globs: expand and READ the match list — is the set the one you meant?
# `find -name`, not a shell glob: under zsh a non-matching brace arm aborts
# the whole line before anything prints.
find "$LIB"/core/skills "$LIB"/usage/skills -maxdepth 1 -name '<your-glob>'

# CLI: the subcommand and the flag both have to exist.
ai-hats <subcommand> --help
```

Quantifiers and counts have no one-liner: for "every X does Y" find the branch
that breaks it; for a number, count it or drop it.

Surface anything unresolved **before commit** — fix the doc, file a backlog
item, or delete the claim.

### INDEX.md freshness

When a doc task **adds, removes, or renames** a file under `docs/`, or
significantly restructures an existing one (new top-level section, renamed
anchor), update `docs/INDEX.md`:

- Add the file to the **Companion docs catalog** table with topic and
  when-to-read.
- If the file is relevant to a specific wizard step — add it under the
  **Wizard companion docs** section too.
- Remove the entry on file deletion; update the row on rename.

Mechanical enforcement: `pre-commit-docs-index.sh` (from the `git-mastery`
skill) blocks the commit when staged changes add / delete / rename
`docs/*.md` without `docs/INDEX.md` also being staged. Content-only
edits (status `M`) do not trigger the hook. This instruction explains
the **why**; the hook is the safety net.

Override (rare, only after user confirmation that the catalog drift is
intentional): `AI_HATS_DOCS_INDEX_ACK=1 git commit ...`.

### Worked example

**A sync-pass on `how-to-feedback-loop.md`.** The doc referenced a
`hypothesis-workflow` skill that does NOT exist in `library/`. Phantom from
an earlier era — the predecessor never grep-verified. Caught at round-2 user
review. Same root cause as a stale `reflect all` → `judge` role mention
elsewhere in the same doc.

Applies to translation tasks and drift-fix tasks alike — verification is orthogonal to text content.

---

## Anti-Patterns

- **Settling style silently** — landing the PR and discovering 6 style questions only in review.
- **Enumerating all ≥6 items** because they were listed in the task description — enumeration ≠ contract.
- **Verifying only the names** on a doc that also asserts a glob, an "every"/"always", or a count — they rot the same way (rule `rule_verify_authored_claims`).
- **Lifting precedent doc structure** without asking "keep this or simplify?" — precedent drift bypasses user approval.
- **Bundling style decisions into review feedback** — pay 2-3 rounds upstream of writing.

## Completion

A doc task passes this protocol when:

- `plan.md` has a `## Style conventions` section resolving the recurring six plus any further fork this doc raises.
- If enumeration ≥6: triage was performed and ≤5 items remain (or user explicitly waived).
- Every checkable claim in the doc — name, glob, quantifier, count — was unrolled against source.
- Numbered-refs convention from CONTRIBUTING followed (if cross-doc/cross-file/fixture links).
- `docs/INDEX.md` updated when the task adds, removes, or renames a file under `docs/`.

## See also

- `rule_verify_authored_claims` — the invariant §3 applies; covers injections and `SKILL.md` too, which this skill does not.
- `design-minimalism` — same upstream principle (curate, don't enumerate), applied at design phase.
- `scope-guard` — implementation-stage scope discipline.
- `CONTRIBUTING.md#documentation-references` — numbered-refs format spec.
- `docs/glossary.md` — terminology source-of-truth.
- `docs/INDEX.md` — wizard companion-docs catalog (must stay in sync; enforced by `pre-commit-docs-index.sh`).
