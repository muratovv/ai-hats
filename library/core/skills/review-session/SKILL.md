---
name: review-session
description: Single-pass review of one session run, producing a hats-reflect-session/v1 document (summary, observations, one hypothesis verdict per active HYP, proposal actions). Use when running as reflect-session or session-reviewer over a specific session id.
---
# Review Session

Single-pass review of one `<ai_hats_dir>/sessions/runs/session_<sid>/` — produces a
`hats-reflect-session/v1` document. Composes **review-hypothesis** (one
verdict per active HYP) + **review-proposal** (vote/create on improvement
opportunities) + a free-form summary and observations.

> **Harness shell prelude.** Before any `ai-hats` invocation:
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }  # HATS-790: no bin/ai-hats console script
> ```

## When to Use

You are running as `reflect-session` or `session-reviewer`, with a
specific session id (`$SID`) and inputs already inlined in the prompt:

- Active hypotheses (`<ai_hats_dir>/tracker/hypotheses/*.yaml` with `status: active`)
- Open proposals (`<ai_hats_dir>/tracker/backlog/proposals/*.yaml` with `status: open`)
- Session evidence: `audit.md` and `metrics.json`

Factual fields (project, role, date, metrics, artifacts, links) are
computed by the runner — do NOT emit them in your output frontmatter.

## Output contract (STRICT)

Emit a YAML document between `BEGIN_REFLECT_SESSION_RETRO` and
`END_REFLECT_SESSION_RETRO` markers. The frontmatter MUST be a YAML
mapping containing EXACTLY these keys:

```yaml
summary: "<one-paragraph narrative of what happened this session>"
observations:
  - "<free-form behavioural note>"
  # 0..6 bullets typical
hypothesis_verdicts:
  - hyp_id: HYP-NNN
    verdict: confirmed | refuted | inconclusive | n/a
    evidence: "<one-line cite from audit.md or metrics.json>"
    recommendation: close_confirmed | close_refuted | keep | extend_window
  # ONE entry PER active HYP — no skips
proposal_actions:
  - action: created | voted
    prop_id: PROP-NNN
  # mirror of CLI calls you ran
self_problems:
  - PROP-NNN  # meta-proposal refs (created when YOU were the problem)
```

No body is required. The runner merges your output with the factual
fields before persisting; extras in the frontmatter are rejected.

## Protocol (in order)

1. **Read evidence.** `audit.md` + `metrics.json` are inlined in the
   prompt — write `summary` (one paragraph) and `observations` (0–6
   bullets).
2. **Sweep active hypotheses.** For each active HYP, follow
   **review-hypothesis** to pick a verdict and persist via
   `ai-hats task hyp append-verdict` (signal-bearing verdicts only).
   Mirror every verdict (including `n/a`) in `hypothesis_verdicts`.
3. **Triage improvement ideas.** For each idea, follow
   **review-proposal**: read the open inbox first, then vote on a
   matching PROP or create a novel one. Mirror every action in
   `proposal_actions`.
4. **Self-meta.** If you cannot follow this format or hit a meta-problem
   (ambiguous instruction, unparseable inbox, conflicting signal) — file a
   meta-proposal via **review-proposal** with
   `--category process --target <your-role>`. Reference its `PROP-NNN` in
   `self_problems`. NEVER silently drop entries.

## Scope

You DO NOT mutate `<ai_hats_dir>/tracker/backlog/tasks/*` or other project state directly.
All side effects go through `ai-hats task hyp append-verdict` /
`ai-hats task proposal *` CLIs (per **rule_backlog_discipline**).

## Examples

### ✓ Good: complete sweep

3 active HYPs, 0 open PROPs, mid-difficulty session. Output has 3 entries
in `hypothesis_verdicts` (one verdict each, with cited evidence), empty
`proposal_actions`, empty `self_problems`. Runner accepts the document.

### ✗ Bad: skipped HYP

Output omits HYP-005 from `hypothesis_verdicts` because "no evidence in this
session". Runtime post-validator rejects this and auto-creates a
meta-proposal.

**Correct response:** emit `n/a` (if the session physically cannot test the
HYP) or `inconclusive` with evidence "session has no relevant phase to
evaluate".
