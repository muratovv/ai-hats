# Rule: The Worktree Is the Default

Any non-trivial code/config change happens in an **isolated git worktree**, never in
the MAIN checkout. This is mechanically enforced: the `worktree-isolation` PreToolUse
gate **hard-denies** an Edit/Write to a code/config file in the MAIN checkout — in both
interactive and headless sessions (HATS-889; a non-blocking nudge here was provably
ignored, PROX-375).

## The constraint

- **Default to a worktree.** On a deny, switch into a worktree and continue there
  (`ai-hats wt create <type>/<name>` from the main repo on master, or `ai-hats wt status`
  if one is already active) — do not retry, rephrase, or wrap the edit.
- **Do NOT ask permission to edit MAIN as a shortcut.** Creating a worktree is one
  command and is *cheaper* than asking; an agent that asks the supervisor to skip the
  worktree — instead of just making one — is the exact failure this rule prevents. Ask
  only for a **genuine direct-master change** (a docs/hotfix that belongs on the base
  branch) or when a worktree is truly impossible.
- **The escape is supervisor-only.** Only the supervisor may authorize a direct-MAIN
  edit, via `AI_HATS_WT_GATE_OFF=1`. The agent **MUST NOT** set it for its own edits
  (same discipline as `AI_HATS_SHARED_STATE_ACK` in `rule_pause_before_shared_state_write`).

Lifecycle + commands (create → merge → discard) live in skill `worktree-isolation`; this
rule is the constraint, that skill is the procedure. Trace: HATS-889.
