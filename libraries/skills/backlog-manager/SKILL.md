# Backlog Manager

Manage project backlog using YAML task cards in `.agent/backlog/tasks/`.

## Capabilities
- Create task cards with proper state machine initialization
- Transition tasks through states: brainstorm → plan → execute → review → done
- Break down large tasks into subtasks with delegation recommendations
- Generate STATE.md from current task state
- Attach plans, retros, and artifacts to task directories

## Usage
When asked to manage tasks, use the task card system in `.agent/backlog/tasks/`.
Each task gets its own directory with `task.yaml` and related artifacts.

## State Machine
Valid transitions:
- brainstorm → plan, blocked
- plan → execute, blocked
- execute → review, blocked, failed
- review → done, failed
- blocked → brainstorm, plan, execute (return to previous)
- failed → brainstorm (start over)
