# review-proposal — field reference & worked examples

Companion to `../SKILL.md`.

## Field reference

| Field | Required | Notes |
|---|---|---|
| `--title` | yes | Imperative, concrete, ≤100 chars |
| `--category` | yes | `rule`, `skill`, `code`, `process`, `doc` |
| `--target` | yes | The thing being changed (file path, skill/rule/role name) |
| `--description` | yes | What the change is (what, not why) |
| `--rationale` | yes | Why — cite session evidence. If the claim is regress/pain/waste, cite a concrete cost (tests, iterations, hours, incident, plan pivots); uncited claims may be closed earlier by judge (see Step 3). |
| `--related-hypotheses` | optional | Comma-separated `HYP-NNN[,...]` |
| `--session` | yes | Source session id |

## Examples

### ✓ Good: vote on similar

Existing `PROP-003` open: `category=rule, target=rule_backlog_discipline`,
covers your concern. → vote with one-line reasoning.

### ✓ Good: create novel

Inbox has nothing on `target=src/ai_hats/cli/_helpers.py`. You found that
`exec_claude_with_retro` ignores `--prompt-suffix`. → create with
`category=code`.

### ✓ Good: cost-cited PROP

PROP-036 — `category=process`, target `plan-stage data-shape verification +
signature-widening grep habit`. Rationale cites concrete cost:
**`9-test breakage`** + **one Phase-1 architectural pivot**. Judge gives
the PROP patience (multi-sweep observation) because the cost is
quantified — signal vs noise is clear.

### ✗ Bad: uncited pain claim

PROP filed with `--rationale "process feels wrong; we waste time"` and
no cited cost. → judge `defer`s it on the next sweep (or `reject`s if
no votes accumulate). Either re-file with cited cost (tests broken,
iterations measured) or vote on an existing cost-cited PROP that covers
the same ground.

### ✗ Bad: duplicate creation

Inbox already has `PROP-003` covering rule_X. You create `PROP-008` about
rule_X with a slightly different title. **Outcome**: vote fragmentation,
triage gets harder. → vote on PROP-003 instead.

### ✗ Bad: meta-proposal as blame

Bad: `--title "reflect-session is broken"`, no actionable description.
Good: `--title "reflect-session skill missing pointer to inbox-first rule"`,
description names the missing line, rationale points at audit/Turn evidence.
