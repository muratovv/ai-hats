---
name: hunk-review-comments
description: "Drain human (HITL) hunk code-review notes (.hunk/notes.json) before any merge/finalize. Distinct from inline document comments (<comment>): this handles code review notes in .hunk/notes.json. Triggered when .hunk/notes.json exists or supervisor gives verbal review cues ('поревьюил', 'оставил комменты', 'оставил замечания', 'посмотрел код'). Cue means drain-now (consume→fix→report), not merge-approval."

ai_hats:
  # HATS-818 / ADR-0012 carry-OUT hook. ai-hats materializes drain-review.sh and
  # runs it before every worktree teardown so the gitignored .hunk/notes.json
  # sidecar is backed up + cleared first (via the DOTS-157 hunk-notes.sh consume)
  # — the consume that never ran in the incident. Fail-closed: a failed drain
  # aborts the teardown, preserving the worktree.
  worktree:
    wt_out:
      - script: hooks/drain-review.sh
        on: [merge, discard, cleanup]
---

# hunk-review-comments

Drain the human code-review comments hunk leaves in `<worktree>/.hunk/notes.json`,
address each, and report — so sticky notes never pile up, re-trigger, or drift off
their `file:line` anchors.

## Two Review Flows & When to Use

There are two distinct review flows in the workspace — do NOT confuse them:
1. **Hunk Code Review (`hunk-review-comments`) — THIS SKILL**: Code review comments left by the supervisor in a `.hunk/notes.json` sidecar. Verbal cues in chat ("поревьюил", "оставил комменты", "оставил замечания", "hunk review") or the presence of `.hunk/notes.json` mean: run `hunk-notes.sh consume` from the worktree → address each note in code → report.
2. **Inline Document Comments (`comment-block-protocol`)**: Inline review threads inside any file marked either with `<comment>...</comment>` tags OR nvim-inserted blocks:
   ```
   \t\t USER: #N <comment>
   ```
   Action: quote → discuss → resolve → delete comment block from file.

### Trigger Checklist for Hunk Review
- A `.hunk/notes.json` sidecar exists in a worktree Fedor reviewed via hunk/wtr.
- The supervisor asks to address / pick up / clear hunk review comments.
- The supervisor signals they reviewed code — verbally ("reviewed" / «поревьюил» / «оставил комменты» / «посмотрел») or otherwise. Treat this as the trigger to **drain**, never as approval to merge/finalize.
- Skip when there is no sidecar, or the review is an inline file comment block (`<comment>` or `USER: #N`), not a hunk worktree code review.

## Order: drain BEFORE any finalize
The `.hunk/notes.json` sidecar is **gitignored and per-worktree** — `ai-hats wt
merge` / `wt discard` delete the worktree and the notes with it, unrecoverable
(the `/tmp/review/<wt-id>` backup is written only *by a consume*, which then has
not run). So:

> **Never** run `ai-hats wt merge`, `ai-hats wt discard`, `ai-hats task
> transition <ID> done`, or any worktree teardown / task finalize while a
> non-empty `.hunk/notes.json` is present. Drain it first (consume → fix →
> report).

A "reviewed / поревьюил" signal is the cue to **drain now**, not a green light to
merge (HATS-818).

## Automatic backstop (wt_out hook)
This skill ships a `wt_out` worktree hook (`hooks/drain-review.sh`, ADR-0012) that
ai-hats runs **before every teardown** (`wt merge` / `wt discard` / `cleanup`). It
runs `hunk-notes.sh consume` against the worktree being torn down, so the sidecar
is always backed up to `/tmp/review/<wt-id>/` and cleared first — the notes are
**never irrecoverable**, even if a drain was forgotten. The hook is *fail-closed*:
if the drain fails (consume errors, or silently leaves the sidecar — e.g. `jq`
missing), the teardown **aborts** and the worktree is preserved; force with
`ai-hats wt … --skip-hooks` only if you accept the loss. Each run appends a triage
line to `/tmp/review/drain-review.log`.

This is a safety net, **not** a substitute for the discipline above: the hook
backs notes up but does **not** address them. Still drain → fix → report yourself;
a "reviewed" signal means drain-now, not merge.

## Procedure
1. **Consume (one step).** Run `hunk-notes.sh` from the
   worktree — or `--branch <name>` / `--wt <name>` to target another branch's
   review. It prints pending comments as TSV `id⇥file:line⇥summary`, backs them
   up to `/tmp/review/<wt-id>/`, and clears the sidecar in the same step. There is
   no separate clear to remember — reading *is* clearing.
2. **Address each.** Treat the TSV as your worklist and resolve every comment in
   the code. The `/tmp` backup holds the full text if you need to re-read it.
3. **Report.** Append a plain-text resolution summary to the active task
   `work_log` via `ai-hats task log <ID> "<summary>"` — one line per comment:
   what it asked, what you did (or why deferred).
4. **On interrupt.** If the session dies mid-fix, run
   `hunk-notes.sh restore` to put the backup back into the
   sidecar so the review is not lost.

## Completion
- `notes.json` is gone (consumed); every comment is addressed or explicitly
  deferred in the report.
- A plain-text resolution summary is in the active task `work_log`.

## Anti-Patterns
- **Hand-editing `notes.json`.** Let the CLI own parse/clear; manual edits risk
  corrupting a sidecar a live hunk session may rewrite.
- **Reading without consuming.** Notes are sticky+reload — peeking without going
  through `hunk-notes.sh` leaves them to re-trigger next session.
- **Tracking per-comment status here.** Cross-session status / partial resolution
  is DOTS-166's job, not this skill's — a consume drains the whole review.
- **Finalizing before draining.** Running `wt merge` / `wt discard` /
  `transition done` with a non-empty `.hunk/notes.json` present destroys the
  gitignored sidecar — the review is lost (HATS-818). Consume → fix → report
  first.
