# Rule: Backlog Discipline

1. **CLI-only.** All backlog operations via `ai-hats task` CLI. Never read or edit `.agent/backlog/tasks/<ID>/task.yaml` directly.
2. **Work log cadence.** Log after every significant action: approach changes, file deletions, branch operations, milestone completions.
3. **State transitions immediate.** Transition state when work changes phase — no stale states.
4. **Completion gate.** Task is `done` only when: state is `done`, work_log has a final entry, STATE.md is synced.

For CLI commands, lifecycle, and plan-flow procedures → skill **backlog-manager**.
