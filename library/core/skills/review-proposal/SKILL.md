---
name: review-proposal
description: Triage one improvement opportunity — vote on an existing PROP or create a novel one via ai-hats task proposal. Use when you spot an improvement (rule/skill/code/process/doc) during a session review, or are sweeping the open proposal inbox for triage.
---
# Review Proposal

Triage one improvement opportunity → vote on existing PROP or create new.
Role-agnostic: same procedure whether you are running as `reflect-session`,
`session-reviewer`, `judge`, or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }  # HATS-790: no bin/ai-hats console script
> ```

## When to Use

Boundaries & disambiguation (the description states the trigger):

- **Vote over create when in doubt.** A proposal is "similar" if it shares
  `category` + `target`; duplicates fragment the signal. Default to a vote.
- **Triage status-flips (`accepted`/`rejected`/`deferred`/`duplicate`) are
  for the closing role only** (typically `judge`/triage). Reviewers vote;
  they do not close the inbox.
- **When YOU are the blocker** (unparseable inbox, conflicting format) —
  file a meta-proposal (Step 4), never silently drop the entry.

## Procedure

### Step 1 — Read the inbox first

```bash
ah task proposal list --status open
```

A proposal is "similar" if it covers the same change (same `category` +
`target`). **Vote rather than create whenever in doubt** — duplicates
fragment the signal.

### Step 2a — Vote (preferred)

```bash
ah task proposal vote --prop PROP-NNN \
  --session "$SID" --reasoning "<one-line: why you agree>"
```

### Step 2b — Create only if novel

```bash
ah task proposal create \
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
ah task proposal status --prop PROP-NNN --status accepted
ah task proposal status --prop PROP-NNN --status rejected
ah task proposal status --prop PROP-NNN --status deferred
ah task proposal status --prop PROP-NNN --status duplicate
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
ah task proposal create \
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

## Field reference & examples

The full `--field` reference table and worked examples (vote-on-similar,
create-novel, cost-cited PROP, uncited-pain, duplicate, meta-as-blame)
live in [`references/examples.md`](references/examples.md).
