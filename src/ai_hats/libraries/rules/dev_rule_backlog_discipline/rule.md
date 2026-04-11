# Rule: Backlog Discipline

1. **CLI Only**: All backlog operations go through `ai-hats task` CLI.
   Never read or edit task YAML files directly. Use:
   - `ai-hats task show <ID>` to inspect a task
   - `ai-hats task list` to browse (supports `--state`, `--priority` filters)
   - `ai-hats task create` to create tasks
   - `ai-hats task transition <ID> <state>` to change state
   - `ai-hats task log <ID>` to add work log entries
   - `ai-hats task update <ID>` to modify fields
   - `ai-hats task sync` to reconcile STATE.md and backlog.md

   Bad:
   ```
   Read .agent/backlog/tasks/PROJ-074/task.yaml
   Edit task.yaml → state: done
   ```
   Good:
   ```
   Bash: ai-hats task show PROJ-074
   Bash: ai-hats task transition PROJ-074 done
   ```
2. **Work Log Cadence**: Log after every significant action: approach changes,
   file deletions, branch operations, milestone completions.
   If an action changes the task's direction or state, log it.
3. **State Transitions**: When work changes phase (approach dropped, task completed,
   blocked on external), transition the state immediately.
   Do not leave stale states.
4. **Completion Gate**: A task is not done until: state is `done`, work_log has
   a final entry, and STATE.md is synced.
