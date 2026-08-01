# Reflect pipeline

Subcommands of `ai-hats reflect` cover the retrospective and backlog triage lifecycle:

- `reflect session` — per-session `session-reviewer` run.
- `reflect hypothesis` — two-phase bulk triage of active HYP + open PROP (`judge-auditor` Phase 1 -> `judge` Phase 2).
- `reflect role <target>` / `reflect roles` — role coherence audit (`role-judge`).
- `reflect issue <observation>` — Haiku-assisted observation intake (`hypothesis-intake`).
- `reflect commit` — bulk update proposal statuses.
- `reflect all` — deprecated single-phase triage (superseded by `reflect hypothesis`).

> Full CLI reference (signatures + flags) — `ai-hats --tree` (subtree: `ai-hats --tree reflect`).

**`reflect session` is not the only producer of `retros/sessions/<id>.md`.** `ai-hats session retro [SESSION_ID] [--last]` (`src/ai_hats/cli/session.py:29`) instantiates `SessionReviewRunner` directly and writes the same `hats-session-review/v1` artifact — bypassing the `reflect-session` pipeline, and with it the harness policy, the verdict harvest and the harness check described below. Reach for it only when you want the document alone.

## Pipeline overview

### `ai-hats reflect session`

Post-session retrospective flow (single LLM call under `session-reviewer`). Factual fields (metrics, files_changed, commits, tasks_closed, links) are computed by pure-Python before the LLM call.

```
session end
  └─ pipeline step `maybe_spawn_session_reviewer`     # NOT a shell hook
       (MaybeSpawnSessionReviewer, wired in finalize-hitl.yaml
        and finalize-subagent.yaml; imports make_decision +
        _spawn_session_reviewer_background from retro/auto_retro.py)
       └─ if decision=run:
            ├─ background=false → run_session_review(...) in-process
            │                     (HATS-1402; HATS_SKIP_RETRO set/popped
            │                      around the call via try/finally)
            └─ otherwise → _spawn_session_reviewer_background
                 (Popen, detached, env: HATS_SKIP_RETRO=1)
                   python -m ai_hats.cli.reflect_session_main <sid>

  reflect_session_main.run_session_review(sid, max_retries, project_dir)
    ├─ PipelineHarness(reflect-session).run(...)      # the harness layer
    │    step `run_session_review` → SessionReviewRunner.run(sid)
    │      1. compute_facts(project_dir, sid)         # pure-Python
    │      2. SubAgentRunner → role=session-reviewer  # one LLM call
    │      3. merge facts + analysis → SessionReviewV1
    │      4. write <ai_hats_dir>/sessions/retros/sessions/<id>.md
    │         (schema: hats-session-review/v1)
    ├─ _maybe_harvest_verdicts → _harvest_verdicts   # pure-Python
    │    non-`n/a` hypothesis_verdicts → append_verdict into each
    │    HYP's validation_log (actor=rack:session-reviewer).
    │    Runs before either error branch below (HATS-1422)
    ├─ on HarnessReliabilityError → file ONE meta-proposal
    │    (target=harness-incident) and `return 2` — the harvest above
    │    already ran; only the harness check below is skipped
    └─ _harness_check (pure-Python)
         missing/empty/incomplete → file ONE meta-proposal
           (category=process, target=session-reviewer,
            failed_session_id=<sid>)
```

Triggers:

- **Auto** on session end (when `feedback.session_retro.policy=always`, or `policy=smart` with the `smart_threshold` met — `off` and `hint` never spawn a run). Detached background process, unless `background: false` selects the in-process branch.
- **Manual** via `ai-hats reflect session --session <id>` (foreground; harness check skipped).

`_maybe_harvest_verdicts` (HATS-1369) auto-persists every non-`n/a` verdict from the saved doc's `hypothesis_verdicts` into the matching HYP's `validation_log` (`session_id` = the reviewed session, `evidence`/`recommendation` copied verbatim), independent of whether `_harness_check` also reports missing coverage for other active HYPs. This is what makes an unattended HITL/subagent session — no manual reflect step — actually land a `validation_log` entry, so the existing `quorum_autoclose` sweep can act on it. It survives **both** error branches (HATS-1422): the doc is loaded and harvested before either is handled, so a `SessionReviewError` or a harness-layer failure — subprocess timeout, zero-output guard — no longer drops verdicts that are already on disk. A stale or partial doc still carries verdicts worth harvesting. `ai-hats reflect hypothesis` (below) remains the judge-driven, HITL-reviewed path to the same field — the two are independent writers, distinguished by actor (`rack:session-reviewer` vs `rack:reflect`).

Meta-proposals are deduped per `(failed_session_id, target)` pair, not per session — the `harness-incident` and `session-reviewer` facets coexist, so one session id can carry two.

### `ai-hats reflect hypothesis` (HATS-513 / ADR-0007)

Two-phase bulk triage of accumulated HYP and PROP backlog:

1. **Phase 1 (`judge-auditor`, headless, read-only):** Pipeline `reflect-hypothesis-phase1` generates draft report with proposed verdicts and CLI mutations at `<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`.
2. **Phase 2 (`judge`, HITL):** Pipeline `reflect-hypothesis-phase2` inlines the draft body and an open-PROP digest, supervisor discusses + ack's mutations, `judge` executes CLI ops and writes report to `<ai_hats_dir>/sessions/retros/judge/<ts>-report.md`.

With `--headless`: runs Phase 1 only (CI/cron-safe).

The Phase 2 preamble carries two substitutions: `{draft_body}` and `{inbox_digest}` (`_build_inbox_digest`). The digest is a compact open-PROP inventory — counts, plus leaders by votes and by age — because Phase 2 otherwise sees only what Phase 1 chose to mention, which is how a judge session missed a 136-card inbox (HATS-1385). An overridden injection that drops `{inbox_digest}` gets the digest appended and a warning, never silence.

Both this command and `reflect all` also write a pre-flight handoff to `<ai_hats_dir>/sessions/retros/reflect-all/<ts>-handoff.md` (`_build_handoff`, `src/ai_hats/cli/reflect.py:900`) and share the `judge/<ts>-report.md` namespace.

### `ai-hats reflect role <target>` / `reflect roles`

Audits target role composition for contradictions against project context (`./CLAUDE.md`, `.agent/ai-hats/user-rules/*.md`). Pipeline `reflect-role` materializes layered composition breakdown to `<ai_hats_dir>/sessions/runs/pipeline_runs/reflect-role/<sid>/composed/<target>/` and runs `role-judge`.

The report lands at `<ai_hats_dir>/sessions/retros/role-coherence/<ts>-<target>.md` **only because the role is instructed to write it** (`core/roles/role-judge/config.yaml`, `core/initial_injections/reflect-role.md`). Unlike the phase1 / phase2 / reflect-all pipelines, `reflect-role.yaml` carries no `save_artifact` step — its three steps are `compose_role`, `resolve_prompt`, `launch_provider` — so nothing persists the report if the model does not call Write.

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
      judge/<ts>-report.md             # JudgeReport (Phase 2 AND reflect all)
      role-coherence/<ts>-<target>.md  # RoleCoherenceReport (written by the role, not by a step)
      reflect-all/<ts>-handoff.md      # pre-flight handoff (reflect all AND reflect hypothesis)
```

## Schema dispatch

`src/ai_hats/retro/loader.py` routes by the family of the `schema:` field — the
`/vN` suffix is stripped before lookup, so the registry keys carry no version:

| Family                 | Model              | Producer                              |
| ---------------------- | ------------------ | ------------------------------------- |
| `hats-session-review`  | `SessionReviewV1`  | session-reviewer (current)            |
| `hats-reflect-session` | `ReflectSessionV1` | historical (pre-HATS-252) — read-only |
