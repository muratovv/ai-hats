---
name: ui-ux-review
description: "Two-mode UI/UX skill — guide mode applies cognitive UX rules while building (do/don't per page type), review mode audits an existing interface into P0/P1/P2 findings with executable fixes + acceptance criteria. Use when building or reviewing any web/app UI, when an LLM-generated interface 'works but looks like a prototype', or before shipping a user-facing screen."
license: MIT
---
# UI/UX Review

Apply cognitive UX rules while building, and audit finished UI into prioritized,
fixable findings. These rules are testable facts (contrast ratios, target sizes,
choice counts) — not taste.

## When to Use
Two modes, pick by intent:
- **Guide** — you are *building* UI: apply the rules proactively.
- **Review** — you are *auditing* existing UI: produce P0/P1/P2 findings.

Skip for backend/API work or bugfixes with no visual/interaction surface. This
skill judges UI **quality** (is it usable and clear); for **correctness** (does
the app actually render and behave in a browser) use **webapp-testing**.

## The hard rules (cognitive — measurable, not aesthetic)
1. **Contrast** — body text ≥ 4.5:1, large text/icons ≥ 3:1 (WCAG AA).
2. **One primary action per view** — size/weight/color encode importance, not decoration.
3. **Spacing on a scale** — 4/8px steps; group related, separate unrelated (proximity).
4. **Touch/click targets ≥ 44×44px** with spacing (Fitts's law).
5. **Limit choices per step** (Hick's law) — progressive disclosure over walls of options.
6. **Recognition over recall** — show options; don't make users remember.
7. **Always give feedback** — visible system status for every action (≤100ms perceived-instant; spinner/skeleton past ~1s).
8. **Prevent errors first** — constrain input; on failure be specific + recoverable.
9. **Be consistent** — same thing looks/behaves the same; match platform conventions (Jakob's law).
10. **Constrain the palette** — grayscale-first, limited type scale, add color/depth last and on purpose.

Full canon (Refactoring UI workflow, Nielsen's 10 heuristics, typography
scales, microinteractions, Lean UX) → `references/ux-canon.md`.

## Guide mode — do/don't per page type
| Page type | Do | Don't |
|---|---|---|
| **Landing** | one clear CTA, value above the fold, generous whitespace | competing CTAs, dense walls of text |
| **Form** | label every field, inline validation, group + order logically, show progress | bare placeholders-as-labels, validate only on submit |
| **Dashboard** | lead with the answer/number, then detail; consistent card rhythm | chart soup with no hierarchy or units |
| **List/Table** | scannable rows, right-align numbers, empty + loading states, sort/filter affordances | unbounded rows, no empty state, centered numerics |
| **Detail** | primary action obvious, secondary actions de-emphasized, breadcrumb/back | equal-weight action row, no way back |

## Review mode — triage to P0/P1/P2
Severity by user impact:
- **P0** — blocks the task or is inaccessible (contrast failure, broken/too-small target, no feedback on a critical action, keyboard trap, missing label).
- **P1** — friction or confusion (weak hierarchy, inconsistent spacing, ambiguous labels, missing empty/error state).
- **P2** — polish (microcopy, micro-interactions, visual refinement).

Each finding MUST carry: **location** → **why it fails** (cite the rule #) →
**executable fix** → **acceptance criterion**. Example:

> **P0 — Login submit (LoginForm.tsx:42).** Button text #9aa on #b3b is 1.9:1,
> fails rule 1 (AA). Fix: use `--color-text` (#1a1a1a) on the button. Accept:
> axe contrast check passes ≥ 4.5:1.

## Completion
- Guide mode: the page type's do-list is satisfied; the 10 rules hold.
- Review mode: every finding triaged P0/P1/P2 with fix + acceptance criterion;
  all P0s resolved before "done"; P1/P2 listed even if deferred.

## Anti-Patterns
- "Looks fine" with no rule cited — opinion, not review.
- Decorative color/shadow before hierarchy and spacing are right.
- Findings without an executable fix or acceptance criterion (not actionable).
- Treating accessibility (contrast, labels, keyboard) as P2 polish — it's P0.

## Attribution
Distilled from **oil-oil/oiloil-ui-ux-guide** (Apache-2.0) and **wondelai/skills**
(MIT). Not a verbatim copy. Provenance + per-source licenses in `metadata.yaml`
under `upstream:`.
