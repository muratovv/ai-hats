# Reflect-hypothesis — Phase 2 (judge, HITL with supervisor)

You are starting **Phase 2** of a two-phase reflect-hypothesis sweep
(HATS-513 / ADR-0007). Phase 1 (`judge-auditor`, headless, read-only)
already produced the draft below.

Apply **judge-protocol** end-to-end:

- Step 1: read the draft cover-to-cover. The `## Proposed mutations`
  section is a CLI checklist Phase 1 recommends.
- Steps 2 → 3: walk HYPs and PROPs with the supervisor. Execute the
  ack'd CLI mutations from the whitelist (`task hyp append-verdict`,
  `task hyp set-status`, `reflect commit`, `task create`).
- Step 3.5: re-run the counter-pass on any NEW negative observations
  that emerge from dialogue.
- Step 4: write the final report at
  `<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md`
  using the `Write` tool. Wrap the body between the start/end markers
  documented in **judge-protocol** Step 4 (the marker strings are
  load-bearing for pipeline extraction — copy them verbatim from the
  protocol skill, do NOT improvise).

---

## Phase 1 draft

{draft_body}
