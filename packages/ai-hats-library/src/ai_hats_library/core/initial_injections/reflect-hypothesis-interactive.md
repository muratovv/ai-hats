# Reflect-hypothesis — Phase 2 (judge, HITL with supervisor)

You are starting **Phase 2** of a two-phase reflect-hypothesis sweep
(ADR-0007). Phase 1 (`judge-auditor`, headless, read-only)
already produced the draft below.

Apply **judge-protocol** end-to-end:

- Step 0: the open-PROP inventory below is the inbox as of launch. Run
  `rack ls --backlog proposal --state open --all` anyway — it is the
  only check that the digest agrees with the catalog, and the inbox is
  the half of the reflex loop a scoped kickoff silently drops.
- Step 1: read the draft cover-to-cover. The `## Proposed mutations`
  section is a CLI checklist Phase 1 recommends.
- Steps 2 → 3: walk HYPs and PROPs with the supervisor. Execute the
  ack'd CLI mutations from the whitelist (`rack hyp append-verdict`,
  a status move as its named edge — `rack transition <HYP-ID>
  confirm|refute|stall|revive` — `reflect commit`, `rack create`,
  `rack transition <PROP-ID> --link related_tasks:<TASK-ID>`).
- Step 3.5: re-run the counter-pass on any NEW negative observations
  that emerge from dialogue.
- Step 4: write the final report at
  `<ai_hats_dir>/sessions/retros/judge/<UTC-ISO-ts>-report.md`
  using the `Write` tool. Wrap the body between the start/end markers
  documented in **judge-protocol** Step 4 (the marker strings are
  load-bearing for pipeline extraction — copy them verbatim from the
  protocol skill, do NOT improvise).

---

{inbox_digest}

---

## Phase 1 draft

{draft_body}
