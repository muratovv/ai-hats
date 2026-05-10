# Rule: Backlog Discipline

Applies to all three backlog item types — **tasks** (`HATS-NNN`), **hypotheses** (`HYP-NNN`), and **proposals** (`PROP-NNN`).

1. **CLI-only.** All backlog operations via `ai-hats task` CLI (`task ...`, `task hyp ...`, `task proposal ...`). Never read or edit `.agent/backlog/**` or `.agent/hypotheses/**` directly.
2. **Work log cadence.** Log after every significant action on a task: approach changes, file deletions, branch operations, milestone completions. For HYP/PROP, append to `validation_log` / `votes` via CLI.
3. **State transitions immediate.** Transition state when work changes phase — no stale states. Applies to task lifecycle (`brainstorm → … → done`), HYP status (`active → confirmed | refuted | stalled`), and PROP status (`open → accepted | rejected | deferred | duplicate`).
4. **Completion gate.** A task is `done` only when: state is `done`, work_log has a final entry, STATE.md is synced.

For CLI commands, lifecycle details, and plan-flow procedures → skill **backlog-manager**.
