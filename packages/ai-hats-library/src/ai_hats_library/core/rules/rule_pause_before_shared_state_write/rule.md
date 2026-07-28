# Rule: Pause Before Shared-State Write

Shared-state writes (PRs, issues, releases, pushes, sub-agent fan-out) are
visible to humans and downstream tooling the instant they land, and some
have **no undo path**. Before each one: emit a brief message naming the
exact command, then **wait for the user's confirmation in the next turn** —
never act in the turn that announces the action.

| Command                                          | Reversibility                                 | Hook       |
| ------------------------------------------------ | --------------------------------------------- | ---------- |
| `gh pr create` / `gh pr close`                   | reversible                                    | allows     |
| `gh pr merge` (+ `--delete-branch`)              | **irreversible** — commit on default branch   | **denies** |
| `gh issue comment` / `gh release create`         | hard to revert (visible / fetched)            | allows     |
| `git push` to a shared branch                    | hard (rewrites what others build on)          | **denies** |
| `git push --dry-run`                             | writes nothing                                | allows     |
| `git push --force` / `-f` / `--force-with-lease` | **irreversible** — rewrites history           | **asks**   |
| `git push <remote> +<refspec>` / `--mirror`      | **irreversible** — same force, other spelling | **asks**   |
| `git push <remote> :<branch>` / `--delete`       | **irreversible** — ref gone for everyone      | **asks**   |
| `TaskCreate` (sub-agent fan-out)                 | reversible but costly to cancel mid-flight    | allows     |

"asks" means the hook escalates to the user: interactively you get a permission
prompt, and in a headless run (`-p`, cron, CI) the call is **blocked**, since
nobody is there to approve. "allows" means only this rule holds the pause.

**Never chain** a shared-state write with other commands (no `&&`, `||`,
`;`, `|`, `$(...)`, backticks) — one Bash call = one shared-state write at
most. A chained call removes the user's chance to interrupt, and an
irreversible step in the middle of a chain cannot be rolled back.

The `pre_bash_shared_state_guard.sh` PreToolUse hook and the git pre-push
hook are the **backstop** — not permission to skip the pause, and their
absence in a given session is not a signal to skip it.
Consent reaches the hook in exactly two ways, and **neither is available to the
agent** — that is the point:

1. The user answers the permission prompt the hook raises.
2. `AI_HATS_SHARED_STATE_ACK=1` is present in the environment that launched
   Claude Code (the `env` block of `.claude/settings.json`, or an export in the
   launching shell), pre-approving the whole session.

Writing `AI_HATS_SHARED_STATE_ACK=1 <command>` as a prefix on the agent's own
command does **nothing**: the hook runs before that command exists as a process,
so the assignment never reaches it. The hook used to instruct exactly that and
then refuse it, costing turns on work the user had already approved
(HATS-1294) — never reach for that form.

The hook owns this policy alone. Do not add a second gate for these commands
elsewhere: a deny is binary, so the coarser gate silently wins and the
handshake above stops working (HATS-1253).
