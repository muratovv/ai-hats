# Rule: Backlog Discipline

1. **Work Log Cadence**: Update task.yaml `work_log` after every significant action:
   approach changes, file deletions, branch operations, milestone completions.
   If an action changes the task's direction or state, log it.
2. **State Transitions**: When work changes phase (approach dropped, task completed,
   blocked on external), update task.yaml `state` immediately.
   Do not leave stale states.
3. **STATE.md Sync**: After any task state change, update `.agent/STATE.md` to
   reflect the current active task and state.
4. **Completion Gate**: A task is not done until: state is `done`, work_log has
   a final entry, and STATE.md is updated.
