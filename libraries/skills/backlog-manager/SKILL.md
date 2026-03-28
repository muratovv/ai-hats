# Backlog Manager

Orchestrate task lifecycle using YAML task cards in `.agent/backlog/tasks/`.

## When to Use
- Starting any new task or work item
- Managing task state transitions (brainstorm → plan → execute → review → done)
- Coordinating sub-agent delegation

## Task Card

Each task gets a directory: `.agent/backlog/tasks/<ID>/task.yaml` + artifacts.

## State Machine

```
brainstorm → plan → execute → review → done
               ↕       ↕        ↕
            blocked  blocked   failed → brainstorm
```

---

## States & Transitions

### brainstorm

Create or refine the task card. Clarify requirements.

- If requirements are unclear → **request-supervisor**: ask supervisor for context
- Output: task.yaml with clear description and acceptance criteria
- Transition to `plan` when scope is understood

### plan

Draft an implementation plan. Attach to task directory as `plan.md`.

- Break large tasks into subtasks with delegation recommendations
- Output: `.agent/backlog/tasks/<ID>/plan.md`
- Transition to `execute` when plan is ready

### plan → execute

1. Create isolated workspace → **worktree-isolation**: `ai-hats wt create type/TICKET-ID`
2. Work happens in worktree — main branch stays clean

### execute

Active development in the worktree.

- Commit frequently with conventional format → **git-mastery**: `type(scope): description`
- Before requesting anything from user → **request-supervisor**: run the checklist
- Log significant actions in task.yaml `work_log`

### execute → review

1. Ensure all changes committed → **git-mastery**: `git status` clean
2. Update task.yaml work_log with summary of what was done

### review

Analyze quality of work done.

- Run **self-retrospective** if there were problems (failures, backtracks, wasted iterations)
- Update task card with final state description
- Create improvement task cards from retrospective findings (if any)
- Transition to `done` when acceptance criteria met

### review → done

1. Merge worktree back → **worktree-isolation**: `ai-hats wt merge`
2. Update task.yaml: `state: done`, `completed_at: <timestamp>`
3. Update STATE.md

### failed

Task cannot be completed from execute or review.

- **self-retrospective**: mandatory — analyze why it failed
- Worktree: keep for analysis or discard → **worktree-isolation**: `ai-hats wt discard`
- Transition back to `brainstorm` with lessons learned in work_log

### blocked

Task is blocked by external dependency from any active state.

- **request-supervisor**: document what blocks and request from supervisor
- Record blocking reason in task.yaml work_log
- Transition back to previous state when unblocked

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
