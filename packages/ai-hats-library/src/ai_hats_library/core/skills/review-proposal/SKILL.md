---
name: review-proposal
description: Triage one improvement opportunity — vote on an existing PROP or create a novel one via `rack proposal`. Use when you spot an improvement (rule/skill/code/process/doc) during a session review, or are sweeping the open proposal inbox for triage.
ai_hats:
  requires:
    cli:
      - name: ai-hats-rack
        check: "rack --help"
        hint: "pip install ai-hats-rack"
    mcp: []
license: MIT
---

# Review Proposal

Triage one improvement opportunity → vote on existing PROP or create new.
Role-agnostic: same procedure whether you are running as `reflect-session`,
`session-reviewer`, `judge`, or any other reviewer.

> **Harness shell prelude.** Before any `ai-hats` invocation:
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }  # no bin/ai-hats console script
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
rack ls --backlog proposal --state open --all
```

Keep `--all`: the default caps at 30 id-sorted rows, so without it you read
the *oldest* thirtieth of the inbox and conclude you read the inbox. On the
day that was measured, it was 30 of 147.

A proposal is "similar" if it covers the same change (same `category` +
`target`). **Vote rather than create whenever in doubt** — duplicates
fragment the signal.

### Step 2a — Vote (preferred)

```bash
rack proposal vote PROP-NNN \
  --session-id "$SID" --reasoning "<one-line: why you agree>"
```

### Step 2b — Create only if novel

```bash
rack proposal create "<short imperative title>" \
  --category {rule|skill|code|process|doc} \
  --target "<rule/skill/file/process name>" \
  --description "<what the change is — what, not why>" \
  --rationale "<why — cite session evidence>" \
  --failed-session-id "$SID"
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
rack transition PROP-NNN accept
rack transition PROP-NNN reject
rack transition PROP-NNN defer
rack transition PROP-NNN duplicate
```

**Cost-citation heuristic** — drives *how long* a PROP stays open, not
cosmetic framing:

| `--rationale` content                                                     | Triage default                                                                                                                                       |
| ------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Concrete cited cost (`9 tests`, `2h`, `1 incident`, plan pivots)          | **Patience**: keep open across multiple sweep cycles, especially for `rule` / `process` categories. Wait for votes / additional evidence.            |
| Uncited pain claim ("feels wrong", "process is off") open ≥ 1 sweep cycle | **Faster close**: `defer` (if ≥1 vote shows others see something) or `reject` (no votes). Don't let pseudo-pain proposals occupy inbox indefinitely. |

The point: critical-category PROPs deserve long observation windows
**when there's something to observe**. Without a cited cost, the judge
can't tell signal from noise — so the inbox shouldn't keep them
indefinitely.

### Step 3b — Batch-triage the auto-filed cards (judge / triage roles only)

The runtime safety net files a PROP on every zero-output / timeout run
(`harness incident: <sid>`) and every incomplete session review
(`session-reviewer incomplete: <sid>`). They accumulate faster than anyone
reads them — 76 of 147 open when that was measured — and they bury the
hand-authored half. Sweep them as one batch under a shared criterion, the
move the HYP pass made with 61 hypotheses.

A card is auto-noise only when **all four** hold:

1. `target` is `harness-incident` or `session-reviewer` (the filer's constants).
2. Title starts with `harness incident:` or `session-reviewer incomplete:`
   (the filer's template).
3. `votes == []` — a vote means someone independently seconded it.
4. It predates the current sweep window — a freshly filed incident gets one
   sweep of attention before it is swept.

> **Never sweep on a single field.** Every one-field shortcut has a victim,
> measured on the live inbox:
>
> | Shortcut                  | Sweeps | Victim                                                                                                                                                  |
> | ------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
> | `target` alone            | 76     | **PROP-107** (5 votes, the reflex-loop persistence break) and **PROP-080** (4 votes) — a hand-written card *about* the harness wears the harness target |
> | `failed_session_id` alone | 74     | **PROP-119** — Step 2b tells human reviewers to pass `--failed-session-id "$SID"` on ordinary creates, so the field marks authorship, not origin        |
> | all four together         | 70     | none — and it spares PROP-021/093/098/163, auto-shaped cards a reviewer seconded                                                                        |

Print the id set and read it against the Victim column **before** flipping
anything. An id carrying a vote or a hand-written title means the criterion
was mis-applied — not that the card is stale.

```bash
ai-hats reflect commit --reject PROP-029 --reject PROP-031  # … one flag per id
```

Reject (not `duplicate`): they are real, distinct events whose signal is the
*rate*, not the individual card. Give the batch one shared evidence line
naming the criterion and the count, so the sweep is auditable as one decision
rather than N silent ones.

If the same class keeps refilling the inbox after a sweep, the channel is the
defect, not the cards — that is a proposal about the filer, not another sweep.

### Step 4 — Meta-proposal (when YOU are the problem)

If you cannot follow the format, the inbox is unparseable, or the
instructions conflict — **do NOT silently drop the entry**. File a
meta-proposal:

```bash
rack proposal create "<one-line: what failed>" \
  --category process --target <your-role> \
  --description "<what>" --rationale "<why it blocked you>" \
  --failed-session-id "$SID"
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
