# Reflect-hypothesis — Phase 1 (judge-auditor, read-only audit)

You are starting **Phase 1** of a two-phase reflect-hypothesis sweep
(HATS-513 / ADR-0007). The dynamic handoff below lists active
hypotheses and the open proposal inbox.

Apply **judge-auditor-protocol** end-to-end:

- Steps 1 → 1.5 → 2 → 3 → 3.5 → 4.
- No CLI mutations (`base-auditor` L0 contract). Record proposed
  invocations in the draft's `## Proposed mutations` section — Phase
  2 will execute them after supervisor ack.
- Emit the draft as a single block between `BEGIN_JUDGE_DRAFT` /
  `END_JUDGE_DRAFT` markers. The pipeline persists it to
  `<ai_hats_dir>/sessions/retros/judge/<ts>-draft.md`. Do NOT use the
  `Write` tool.

The draft IS this session's retro — do not separately invoke
`self-retrospective`.
