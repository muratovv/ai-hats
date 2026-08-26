# Rule: Pause Before Shared-State Write

Shared-state writes (PRs, issues, releases, pushes, sub-agent fan-out) are
visible to humans and downstream tooling the instant they land, and some
have **no undo path**. Before each one: emit a brief message naming the
exact command, then **wait for the user's confirmation in the next turn** —
never act in the turn that announces the action.

Which commands count is the classifier's call, not this rule's.
`shared_state_classifier.sh` sorts a command into `irreversible` (no undo —
`gh pr merge`, `git push --force` and its other spellings), `gated` (a plain
`git push` to a shared branch), `shared` (`gh pr create` / `close`,
`gh issue comment`, `gh release create` — reversible, so the pause above is the
only thing holding them) or `safe`.

What the hook then DOES with a verdict is the hook's to say, and it says it in
the refusal it prints. This rule deliberately does not restate it: a restatement
drifts, and the drift is invisible — the table that stood here promised a hard
refusal for `gh pr merge` and `git push` where the hook in fact escalates to the
user, so the agent was taught a guardrail stronger than the one it has.
Treat every one of them as blocked until the user answers.

**Never chain** a shared-state write with other commands (no `&&`, `||`,
`;`, `|`, `$(...)`, backticks) — one Bash call = one shared-state write at
most. A chained call removes the user's chance to interrupt, and an
irreversible step in the middle of a chain cannot be rolled back.

The `pre_bash_shared_state_guard.sh` PreToolUse hook and the git pre-push hook
are the **backstop** — not permission to skip the pause, and their absence in a
session is not a signal to skip it. Consent arrives either from the user
answering the hook's prompt, or from an ack already present in
the environment that launched the agent — and every ack granted is journalled.
The hook spells both out in its own refusal; what matters here is that neither
is reachable by the agent. That is the point.

One trap the hook cannot warn you out of in advance. Writing `AI_HATS_SHARED_STATE_ACK=1 <command>` as a prefix on the agent's own
command does **nothing**: the hook runs before that command exists as a process,
so the assignment never reaches it. The hook used to instruct exactly that and
then refuse it, costing turns on work the user had already approved
 — never reach for that form.

The hook owns this policy alone. Do not add a second gate for these commands
elsewhere: a deny is binary, so the coarser gate silently wins and the
handshake above stops working.
