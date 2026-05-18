# Review Proposal

Triage one improvement opportunity → vote on existing PROP or create new.
Role-agnostic: same procedure whether you are running as `reflect-session`,
`session-reviewer`, `judge`, or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> ```

## When to Use

You spotted an improvement opportunity (rule wording, skill update, code
bug, process gap, doc fix), or you are sweeping the open inbox for triage
(`open → accepted | rejected | deferred | duplicate`).

## Procedure

### Step 1 — Read the inbox first

```bash
"$AH" task proposal list --status open
```

A proposal is "similar" if it covers the same change (same `category` +
`target`). **Vote rather than create whenever in doubt** — duplicates
fragment the signal.

### Step 2a — Vote (preferred)

```bash
"$AH" task proposal vote --prop PROP-NNN \
  --session "$SID" --reasoning "<one-line: why you agree>"
```

### Step 2b — Create only if novel

```bash
"$AH" task proposal create \
  --title "<short imperative title>" \
  --category {rule|skill|code|process|doc} \
  --target "<rule/skill/file/process name>" \
  --description "<what the change is — what, not why>" \
  --rationale "<why — cite session evidence>" \
  --related-hypotheses HYP-NNN[,HYP-MMM] \
  --session "$SID"
```

The CLI returns the new `PROP-NNN`.

> **Cost-citation in `--rationale`.** If the rationale is a claim of
> *regress / pain / waste*, cite a concrete cost: hours lost, tests
> broken, iterations wasted, user-facing incident, plan pivots. Uncited
> pain claims still file (signal isn't lost), but they carry less weight
> in triage — judge may close them earlier (Step 3). Cited cost = the
> judge gives the PROP patience. Precedent: **PROP-036**
> (`9-test breakage + 1 plan pivot`).

### Step 3 — Triage open proposals (judge / triage roles only)

If you are the role responsible for closing the inbox (typically `judge`),
flip status after weighing votes/evidence:

```bash
"$AH" task proposal status --prop PROP-NNN --status accepted
"$AH" task proposal status --prop PROP-NNN --status rejected
"$AH" task proposal status --prop PROP-NNN --status deferred
"$AH" task proposal status --prop PROP-NNN --status duplicate
```

**Cost-citation heuristic** — drives *how long* a PROP stays open, not
cosmetic framing:

| `--rationale` content | Triage default |
|---|---|
| Concrete cited cost (`9 tests`, `2h`, `1 incident`, plan pivots) | **Patience**: keep open across multiple sweep cycles, especially for `rule` / `process` categories. Wait for votes / additional evidence. |
| Uncited pain claim ("feels wrong", "process is off") open ≥ 1 sweep cycle | **Faster close**: `defer` (if ≥1 vote shows others see something) or `reject` (no votes). Don't let pseudo-pain proposals occupy inbox indefinitely. |

The point: critical-category PROPs deserve long observation windows
**when there's something to observe**. Without a cited cost, the judge
can't tell signal from noise — so the inbox shouldn't keep them
indefinitely.

### Step 4 — Meta-proposal (when YOU are the problem)

If you cannot follow the format, the inbox is unparseable, or the
instructions conflict — **do NOT silently drop the entry**. File a
meta-proposal:

```bash
"$AH" task proposal create \
  --category process --target <your-role> \
  --title "<one-line: what failed>" \
  --description "<what>" --rationale "<why it blocked you>" \
  --session "$SID"
```

Even if you fail to file the meta-proposal yourself, the runtime
post-validator will create one with `failed_session_id=<sid>` so the
failure surfaces in the inbox.

## Output handoff

How the action is *reported* depends on the calling role:

- **Running as `reflect-session` / `session-reviewer`** — mirror in the
  `proposal_actions` array of the session document; see **review-session**.
- **Running as `judge`** — list each create/vote/status flip in the judge
  report (`<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md`).

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
