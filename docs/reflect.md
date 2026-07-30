# Reflect pipeline

Subcommands of `ai-hats reflect` cover the retrospective and backlog triage lifecycle:

- `reflect session` — per-session `session-reviewer` run.
- `reflect hypothesis` — two-phase bulk triage of active HYP + open PROP (`judge-auditor` Phase 1 -> `judge` Phase 2).
- `reflect role <target>` / `reflect roles` — role coherence audit (`judge-for-role`).
- `reflect issue <observation>` — Haiku-assisted observation intake (`hypothesis-intake`).
- `reflect commit` — bulk update proposal statuses.
- `reflect all` — deprecated single-phase triage (superseded by `reflect hypothesis`).

> Full CLI reference (signatures + flags) — `ai-hats --tree` (subtree: `ai-hats --tree reflect`).

## Pipeline overview

### `ai-hats reflect session`
Post-session retrospective flow (single LLM call under `session-reviewer`). Factual fields (metrics, files_changed, commits, tasks_closed, links) are computed by pure-Python before the LLM call.

```
session_end (hook → auto_retro)
  └─ if decision=run:
       _spawn_session_reviewer_background (Popen, env: HATS_SKIP_RETRO=1)
         python -m ai_hats.cli.reflect_session_main <sid>
           ├─ SessionReviewRunner.run(sid)
           │    1. compute_facts(project_dir, sid)         # pure-Python
           │    2. SubAgentRunner → role=session-reviewer  # one LLM call
           │    3. merge facts + analysis → SessionReviewV1
           │    4. write <ai_hats_dir>/sessions/retros/sessions/<id>.md
           │       (schema: hats-session-review/v1)
           └─ harness_check (pure-Python)
                missing/empty/incomplete → file ONE meta-proposal
                  (category=process, target=session-reviewer,
                   failed_session_id=<sid>; deduped per session)
```

Triggers:
- **Auto** on session-end (when `feedback.session_retro.policy=run` or `smart` threshold met); detached background process.
- **Manual** via `ai-hats reflect session --session <id>` (foreground; harness check skipped).

### `ai-hats reflect hypothesis` (HATS-513 / ADR-0007)
Two-phase bulk triage of accumulated HYP and PROP backlog:

1. **Phase 1 (`judge-auditor`, headless, read-only):** Pipeline `reflect-hypothesis-phase1` generates draft report with proposed verdicts and CLI mutations at `<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`.
2. **Phase 2 (`judge`, HITL):** Pipeline `reflect-hypothesis-phase2` inlines draft body, supervisor discusses + ack's mutations, `judge` executes CLI ops and writes report to `<ai_hats_dir>/sessions/retros/judge/<ts>-report.md`.

With `--headless`: runs Phase 1 only (CI/cron-safe).

### `ai-hats reflect role <target>` / `reflect roles`
Audits target role composition for contradictions against project context (`./CLAUDE.md`, `.agent/ai-hats/user-rules/*.md`). Pipeline `reflect-role` materializes layered composition breakdown to `<ai_hats_dir>/sessions/runs/pipeline_runs/reflect-role/<sid>/composed/<target>/` and runs `judge-for-role`. Report is saved to `<ai_hats_dir>/sessions/retros/role-coherence/<ts>-<target>.md`.

### `ai-hats reflect issue <text>`
Observation intake flow. Pipeline `reflect-issue` runs `hypothesis-intake` (Haiku model): checks active HYPs, deduplicates against recent evidence, and either drafts a new HYP (`action: create`) or appends evidence to an existing HYP (`action: merge`).

### `ai-hats reflect commit`
Bulk-applies proposal status changes (`--accept PROP-X --reject PROP-Y ...`) at the end of interactive chat sessions.

### `ai-hats reflect all` (Deprecated)
Legacy single-phase triage. Replaced by `reflect hypothesis`. Kept for backward compatibility.

## Storage layout

```
<ai_hats_dir>/
  sessions/
    retros/
      sessions/<id>.md                 # SessionReviewV1
      judge/<ts>-draft.md              # JudgeDraft (Phase 1)
      judge/<ts>-report.md             # JudgeReport (Phase 2)
      role-coherence/<ts>-<target>.md  # RoleCoherenceReport
      reflect-all/<ts>-handoff.md      # legacy pre-flight handoff
```

## Schema dispatch

`src/ai_hats/retro/loader.py` routes by `schema:` family:

| Family                    | Model              | Producer                              |
| ------------------------- | ------------------ | ------------------------------------- |
| `hats-session-review/v1`  | `SessionReviewV1`  | session-reviewer (current)            |
| `hats-reflect-session/v1` | `ReflectSessionV1` | historical (pre-HATS-252) — read-only |
