# Reflect pipeline

Two subcommands of `ai-hats reflect` cover the retrospective lifecycle:
`reflect session` (per-session `session-reviewer` run) and `reflect all`
(interactive triage of the accumulated backlog).

> Full CLI reference (signatures + flags) — `ai-hats --tree`.

## Pipeline overview (HATS-252)

Pre-HATS-252 the post-session flow made two LLM calls
(`SessionRetroBuilder` + `reflect-session`). It is now a single LLM call
under the `session-reviewer` role; factual fields (metrics, files_changed,
commits, tasks_closed, links, role, project, date) are computed by pure
Python before the call.

```
session_end (hook → auto_retro)
  └─ if decision=run:
       _spawn_session_reviewer_background (Popen, env: HATS_SKIP_RETRO=1)
         python -m ai_hats.cli.reflect_session_main <sid>
           ├─ SessionReviewRunner.run(sid)
           │    1. compute_facts(project_dir, sid)         # pure-Python
           │    2. SubAgentRunner → role=session-reviewer  # one LLM call
           │    3. merge facts + analysis → SessionReviewV1
           │    4. write .agent/retrospectives/sessions/<id>.md
           │       (schema: hats-session-review/v1)
           └─ harness_check (pure-Python)
                missing/empty/incomplete → file ONE meta-proposal
                  (category=process, target=session-reviewer,
                   failed_session_id=<sid>; deduped per session)

  Side effects during the LLM call (via CLI from inside the sub-Claude):
    - ai-hats task hyp append-verdict ...      (HYP validation_log)
    - ai-hats task proposal create | vote ...  (inbox grow / co-sign)

  Recursion guard:
    HATS_SKIP_RETRO=1 propagates to the sub-Claude session via
    SubAgentRunner; both the shell hook and auto_retro main() honour it
    and write a `recursion-guard` breadcrumb to retro.log.

ai-hats reflect session [--session ID] [--background]
  Manual single-session run of session-reviewer.

ai-hats reflect all [--dry-run]
  Pre-flight (Python): collect active HYP + open PROP into a handoff.md
  Interactive: os.execvp claude with pointer to the handoff.
  reflect commit: bulk-update PROP statuses (accept/reject/defer/duplicate).
```

## `ai-hats reflect session`

Per-session `session-reviewer` run. Output is `hats-session-review/v1`
markdown at `.agent/retrospectives/sessions/<id>.md`.

Triggers:
- **Auto** on session-end (when `feedback.session_retro.policy=run`); detached background.
- **Manual** via `ai-hats reflect session --session <id>` (foreground; harness check skipped).

Validation contract:
- One `hypothesis_verdicts[]` entry per active HYP (no skipping).
- Verdict ∈ `{confirmed, refuted, inconclusive, n/a}`.
- `n/a` only when the session physically cannot test the HYP.
- `summary` is non-empty.
- Self-problems ⇒ reviewer files a meta-proposal via CLI and lists the
  resulting PROP-NNN in `self_problems[]`.
- Single safety net: harness check (pure-Python) at the CLI layer is the
  sole owner of the failure-proposal — no double-fire from the runner.

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
    sessions/<id>.md                 # SessionReviewV1 (single LLM call)
    reflect-all/<ts>-handoff.md      # pre-flight pointer
    reflect-session/<id>.md          # historical only — pre-HATS-252 ReflectSessionV1
```

## Schema dispatch

`src/ai_hats/retro/loader.py` routes by `schema:` family:

| Family | Model | Producer |
|---|---|---|
| `hats-session-review/v1`  | `SessionReviewV1`  | session-reviewer (current) |
| `hats-reflect-session/v1` | `ReflectSessionV1` | historical (pre-HATS-252) — read-only |
