# Reflect pipeline

Three commands cover the retrospective lifecycle. Two are new in HATS-210
(`reflect-session`, `reflect-all`). The legacy `reflect` (bundle/judge/aggregate)
is still available — its removal is a follow-up task.

## Pipeline overview

```
session_end (runtime → auto_retro.make_decision)
  ├─ if decision=run AND mode=LLM:
  │     builder LLM → SessionRetroV1 (.agent/retrospectives/sessions/llm/<id>.md)
  │     reflect-session  ← NEW (HATS-210), spawned background detached
  │       reads .agent/hypotheses/*.yaml (status=active)
  │            + .agent/backlog/proposals/*.yaml (status=open)
  │       writes ReflectSessionV1 (.agent/retrospectives/reflect-session/<id>.md)
  │       side-effects:
  │         - ai-hats hyp append-verdict ...     (HYP validation_log)
  │         - ai-hats proposal create | vote ... (inbox grow / co-sign)
  │       runtime safety net: post-validate output; if absent or invalid →
  │         programmatic meta-proposal w/ failed_session_id=<sid>
  │
  └─ if decision=run AND mode=PROGRAMMATIC:
        builder programmatic → SessionRetroV1   (no reflect-session spawned)

ai-hats reflect-session [--session ID] [--background]   ← NEW
  Manual single-session run of the same role.

ai-hats reflect-all [--dry-run]                          ← NEW
  Pre-flight (Python): collect active HYP + open PROP into a handoff.md
  Interactive: os.execvp claude with pointer to the handoff.
  reflect-all commit: bulk-update PROP statuses (accept/reject/defer/duplicate).

ai-hats reflect [legacy bundle flow]                     ← UNCHANGED
  backfill → bundle → judge per chunk → aggregate → interactive
  TBD removal (HATS-NNN follow-up).
```

## `ai-hats reflect-session`

Per-session judge run. Spawns the **reflect-session** role on a single
`.gitlog/session_<id>/`. Output is `hats-reflect-session/v1` markdown.

Triggers:
- **Auto** on session-end (when `feedback.session_retro.policy=run` AND
  `feedback.session_retro.mode=llm`); detached background.
- **Manual** via `ai-hats reflect-session --session <id>` (foreground).

Validation contract:
- One `hypothesis_verdicts[]` entry per active HYP (no skipping).
- Verdict ∈ `{confirmed, refuted, inconclusive, n/a}`.
- `n/a` only when the session physically cannot test the HYP.
- Self-problems ⇒ judge files a meta-proposal via CLI and lists the
  resulting PROP-NNN in `self_problems[]`.
- Two-layer no-silent-failure: LLM-driven (in-skill) + runtime-driven
  (programmatic post-validation always files a meta-proposal on failure).

## `ai-hats reflect-all`

Manual triage of accumulated backlog. Two stages:

1. Pre-flight builds `.agent/retrospectives/reflect-all/<ts>-handoff.md`
   listing all active HYP and open PROP.
2. `os.execvp` to `claude` with a pointer prompt.
3. After chat: `ai-hats reflect-all commit --accept PROP-X --reject PROP-Y ...`
   flips statuses in bulk.

`--dry-run` builds the handoff but skips the exec — useful for inspection.

## Storage layout

```
.agent/
  hypotheses/
    HYP-NNN-<slug>.yaml             # validation_log appended via CLI
  backlog/
    tasks/                           # untouched
    proposals/                       # NEW (HATS-210)
      PROP-NNN.yaml                  # status: open|accepted|rejected|deferred|duplicate
  retrospectives/
    sessions/<mode>/<id>.md          # SessionRetroV1 (builder)
    reflect-session/<id>.md          # ReflectSessionV1 (HATS-210)
    reflect-all/<ts>-handoff.md      # pre-flight pointer (HATS-210)
    judge/<ts>-judge-NNN.md          # JudgeRetroV1 (legacy)
    aggregated/<ts>-AGG-NNN.md       # AggregationV1 (legacy)
```

## Schema dispatch

`src/ai_hats/retro/loader.py` routes by `schema:` family:

| Family | Model | Producer |
|---|---|---|
| `hats-session-retro/v1` | `SessionRetroV1` | builder |
| `hats-bundle/v1` | `BundleV1` | bundle manager (legacy) |
| `hats-judge-retro/v1` | `JudgeRetroV1` | bundle judge (legacy) |
| `hats-aggregation/v1` | `AggregationV1` | aggregator (legacy) |
| `hats-reflect-session/v1` | `ReflectSessionV1` | reflect-session (HATS-210) |

## Follow-up tasks (HATS-218 children)

- **legacy reflect cleanup**: rm `bundles.py`, `aggregator.py`, `frequency.py`,
  `judge_retro.py`, legacy `cli/reflect.py`, `--chunk` flag, AggregationV1,
  JudgeRetroV1, role `judge`, `BuilderMode.PROGRAMMATIC`. Update docs.
- **`ai-hats reflect-all --interactive`** for single-session focus mode.
- **backfill-style retry mechanism** for failed reflect-session runs:
  enumerate open meta-proposals (`category=process`, `target=reflect-session`,
  `failed_session_id` set) and re-queue.
- **HATS-217**: `ai-hats hyp verdict` deterministic CLI.
