# Rule: Pause Before Shared-State Write

Shared-state writes (PRs, issues, releases, pushes, sub-agent fan-out) are
visible to humans and downstream tooling the instant they land, and some
have **no undo path**. Before each one: emit a brief message naming the
exact command, then **wait for the user's confirmation in the next turn** —
never act in the turn that announces the action.

| Command                                          | Reversibility                                    |
| ------------------------------------------------ | ------------------------------------------------ |
| `gh pr create` / `gh pr close`                   | reversible                                       |
| `gh pr merge` (+ `--delete-branch`)              | **irreversible** — commit on default branch      |
| `gh issue comment` / `gh release create`         | hard to revert (visible / fetched)               |
| `git push` to a shared branch                    | hard (force-push usually blocked)                |
| `git push --force` / `-f` / `--force-with-lease` | **irreversible** — rewrites history              |
| `ai-hats wt merge` / `task transition done`      | commit on base branch — review-gated (HATS-1019) |
| `task transition execute` (from `plan`)          | implementation start — plan-gated (HATS-1106)    |
| `TaskCreate` (sub-agent fan-out)                 | reversible but costly to cancel mid-flight       |

**Never chain** a shared-state write with other commands (no `&&`, `||`,
`;`, `|`, `$(...)`, backticks) — one Bash call = one shared-state write at
most. A chained call removes the user's chance to interrupt, and an
irreversible step in the middle of a chain cannot be rolled back.

The `pre_bash_shared_state_guard.sh` PreToolUse hook and the git pre-push
hook block the irreversible subset as a **backstop** — not permission to
skip the pause, and their absence in a given session is not a signal to skip
it. `AI_HATS_SHARED_STATE_ACK=1` is the consent flag; the agent MUST NOT
self-grant it — set it only on a command the user explicitly approved in this
conversation (the announce→wait handshake above). The same holds for
`AI_HATS_MERGE_ACK=1` (worktree merge consent, HATS-1019) and
`AI_HATS_PLAN_ACK=1` (plan execution consent, HATS-1106), where approval
means the supervisor explicitly approved the plan — then execute with the ack;
anything less is a STOP at plan mode, not execute. Trace: PROP-052.
