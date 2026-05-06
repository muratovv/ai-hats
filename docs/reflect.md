# Reflect pipeline

Two subcommands of `ai-hats reflect` cover the retrospective lifecycle:
`reflect session` (per-session reflect-session run) and `reflect all`
(interactive triage of the accumulated backlog).

> Full CLI reference (signatures + flags) — `ai-hats --tree`.

## Pipeline overview

```
session_end (runtime → auto_retro.make_decision)
  └─ if decision=run:
       SessionRetroBuilder (LLM) → SessionRetroV1
         .agent/retrospectives/sessions/<id>.md
       reflect-session  ← spawned background detached
         reads .agent/hypotheses/*.yaml (status=active)
              + .agent/backlog/proposals/*.yaml (status=open)
         writes ReflectSessionV1 (.agent/retrospectives/reflect-session/<id>.md)
         side-effects:
           - ai-hats task hyp append-verdict ...     (HYP validation_log)
           - ai-hats task proposal create | vote ... (inbox grow / co-sign)
         runtime safety net: post-validate output; if absent or invalid →
           programmatic meta-proposal w/ failed_session_id=<sid>

ai-hats reflect session [--session ID] [--background]
  Manual single-session run of the same role.

ai-hats reflect all [--dry-run]
  Pre-flight (Python): collect active HYP + open PROP into a handoff.md
  Interactive: os.execvp claude with pointer to the handoff.
  reflect commit: bulk-update PROP statuses (accept/reject/defer/duplicate).
```

## `ai-hats reflect session`

Per-session reflect-session run. Spawns the **reflect-session** role on a single
`.gitlog/session_<id>/`. Output is `hats-reflect-session/v1` markdown.

Triggers:
- **Auto** on session-end (when `feedback.session_retro.policy=run`); detached background.
- **Manual** via `ai-hats reflect session --session <id>` (foreground).

Validation contract:
- One `hypothesis_verdicts[]` entry per active HYP (no skipping).
- Verdict ∈ `{confirmed, refuted, inconclusive, n/a}`.
- `n/a` only when the session physically cannot test the HYP.
- Self-problems ⇒ reflect-session files a meta-proposal via CLI and
  lists the resulting PROP-NNN in `self_problems[]`.
- Two-layer no-silent-failure: LLM-driven (in-skill) + runtime-driven
  (programmatic post-validation always files a meta-proposal on failure).

## `ai-hats reflect all`

Manual triage of accumulated backlog. Two stages:

1. Pre-flight builds `.agent/retrospectives/reflect-all/<ts>-handoff.md`
   listing all active HYP and open PROP.
2. `os.execvp` to `claude` with a pointer prompt.
3. After chat: `ai-hats reflect commit --accept PROP-X --reject PROP-Y ...`
   flips statuses in bulk.

`--dry-run` builds the handoff but skips the exec — useful for inspection.

## Storage layout

```
.agent/
  hypotheses/
    HYP-NNN-<slug>.yaml             # validation_log appended via CLI
  backlog/
    tasks/                           # untouched
    proposals/
      PROP-NNN.yaml                  # status: open|accepted|rejected|deferred|duplicate
  retrospectives/
    sessions/<id>.md                 # SessionRetroV1 (builder, LLM)
    reflect-session/<id>.md          # ReflectSessionV1
    reflect-all/<ts>-handoff.md      # pre-flight pointer
```

## Schema dispatch

`src/ai_hats/retro/loader.py` routes by `schema:` family:

| Family | Model | Producer |
|---|---|---|
| `hats-session-retro/v1` | `SessionRetroV1` | builder |
| `hats-reflect-session/v1` | `ReflectSessionV1` | reflect-session |
