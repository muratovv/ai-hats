# Rule: Pause Before Shared-State Write

Shared-state writes (PRs, issues, releases, pushes, scheduled tasks) are
visible to humans and downstream tooling the moment they land. Some of
them have **no undo path**. Confirm explicitly before each one.

## 1. Always pause and name the command

Before any of the following — emit a brief message that names the exact
command, then **wait for the user's confirmation in the next turn**. Do
not act in the same turn that announces the action.

| Command | Reversibility |
|---|---|
| `gh pr create` | reversible (`gh pr close`) |
| `gh pr close` | reversible (reopen) |
| `gh pr merge` | **irreversible** — commit lands on the default branch |
| `gh pr merge --delete-branch` | **irreversible** — branch must be recreated from SHA |
| `gh issue comment` | hard to revert (visible to humans) |
| `gh release create` | hard to revert (consumers may have fetched) |
| `git push` to a shared branch | hard (force-push usually blocked) |
| `git push --force` / `-f` / `--force-with-lease` | **irreversible** — overwrites remote history |
| `TaskCreate` (sub-agent fan-out) | reversible but costly to cancel mid-flight |

## 2. Never chain a shared-state write with other commands

Shared-state writes MUST NOT appear in a compound Bash invocation with
other commands (no `&&`, `||`, `;`, `|`, `$(...)`, backticks). One Bash
call = one shared-state write at most. Reasoning: a chained call removes
the user's chance to interrupt between steps, and a single irreversible
step in the middle of a chain cannot be rolled back.

## 3. Per-command pause format

A valid pause message:

> About to run `gh pr merge 9 --merge --delete-branch`. This is
> irreversible (commit on master + branch deletion). Confirm to proceed?

After the message — **stop**. Do not run the command. Wait for the user's
explicit go-ahead in the next message.

## 4. Hooks are a safety net, not permission

A PreToolUse hook (`pre_bash_shared_state_guard.sh`) and a git pre-push
hook block the irreversible subset (`gh pr merge`, `git push --force`)
when run without acknowledgement. These hooks exist **only** as a backstop
for hook-1 violations. Do not treat their absence in a particular session
as permission to skip the pause.

Override env (`AI_HATS_SHARED_STATE_ACK=1`) exists for the user, not for
the agent. The agent MUST NOT set this env variable for its own commands.
Using the override without an explicit user confirmation in the prior
turn is a Level-2-rule violation regardless of whether the hook fires.

## 5. Why

Two confirmed hypotheses (HYP-026, HYP-027) and one cited proposal
(PROP-052) trace back to a single incident: an agent chained
`gh pr merge 9 --merge --delete-branch && git push && git checkout master
&& git pull` in one Bash invocation. The merge was irreversible; the
branch deletion compounded the loss; the user had no chance to
interrupt. The rule's primary purpose is to prevent that pattern at the
discipline layer; the hook is the second wall.
