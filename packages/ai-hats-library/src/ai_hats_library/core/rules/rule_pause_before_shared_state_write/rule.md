# Rule: Pause Before Shared-State Write

Shared-state writes (PRs, issues, releases, pushes, sub-agent fan-out) are
visible to humans and downstream tooling the instant they land, and some
have **no undo path**. Before each one: emit a brief message naming the
exact command, then **wait for the user's confirmation in the next turn** —
never act in the turn that announces the action.

| Command                                          | Reversibility                               | Hook       |
| ------------------------------------------------ | ------------------------------------------- | ---------- |
| `gh pr create` / `gh pr close`                   | reversible                                  | allows     |
| `gh pr merge` (+ `--delete-branch`)              | **irreversible** — commit on default branch | **denies** |
| `gh issue comment` / `gh release create`         | hard to revert (visible / fetched)          | allows     |
| `git push` to a shared branch                    | hard (rewrites what others build on)        | **denies** |
| `git push --dry-run`                             | writes nothing                              | allows     |
| `git push --force` / `-f` / `--force-with-lease` | **irreversible** — rewrites history         | **denies** |
| `TaskCreate` (sub-agent fan-out)                 | reversible but costly to cancel mid-flight  | allows     |

"denies" means the hook refuses until `AI_HATS_SHARED_STATE_ACK=1` is set on
that one command (HATS-1253). "allows" means only this rule holds the pause.

**Never chain** a shared-state write with other commands (no `&&`, `||`,
`;`, `|`, `$(...)`, backticks) — one Bash call = one shared-state write at
most. A chained call removes the user's chance to interrupt, and an
irreversible step in the middle of a chain cannot be rolled back.

The `pre_bash_shared_state_guard.sh` PreToolUse hook and the git pre-push
hook are the **backstop** — not permission to skip the pause, and their
absence in a given session is not a signal to skip it.
`AI_HATS_SHARED_STATE_ACK=1` is the consent flag; the agent MUST NOT
self-grant it — set it only on a command the user explicitly approved in this
conversation (the announce→wait handshake above).

The hook owns this policy alone. Do not add a second gate for these commands
elsewhere: a deny is binary, so the coarser gate silently wins and the
handshake above stops working (HATS-1253).
