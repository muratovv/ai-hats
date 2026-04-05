---
name: backlog-manager
description: Task lifecycle orchestration via YAML cards (brainstorm→plan→execute→document→review→done)
---
# Backlog Manager

Orchestrate task lifecycle using YAML task cards in `.agent/backlog/tasks/`.

## When to Use
- Starting any new task or work item
- Managing task state transitions (brainstorm → plan → execute → document → review → done)
- Coordinating sub-agent delegation

## Task Card

Each task gets a directory: `.agent/backlog/tasks/<ID>/task.yaml` + artifacts.

## State Machine

```
brainstorm → plan → execute → document → review → done
               ↕       ↕         ↕          ↕
            blocked  blocked   blocked    failed → brainstorm
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

- **Requirement traceability:** If the user listed specific approaches, options,
  or alternatives to consider, create a checklist in plan.md:
  ```
  ## Approaches
  - [ ] Approach A: <name> — explored / rejected with reason
  - [ ] Approach B: <name> — explored / rejected with reason
  ```
  Every user-mentioned approach MUST appear. None may be silently skipped.
  If rejected, document the specific reason.
- Break large tasks into subtasks with delegation recommendations
- Before delegating → **context-handoff**: summarize context for sub-agent
- Output: `.agent/backlog/tasks/<ID>/plan.md`
- Transition to `execute` when plan is ready AND all approaches are addressed

### plan → execute

1. Create isolated workspace → **worktree-isolation**: `ai-hats wt create type/TICKET-ID`
2. Work happens in worktree — main branch stays clean

### execute

Active development in the worktree.

- Check task boundaries before starting → **scope-guard**: verify scope alignment
- Commit frequently with conventional format → **git-mastery**: `type(scope): description`
- Before requesting anything from user → **request-supervisor**: run the checklist
- On context pressure → **context-reset**: save state, write handoff, hand off cleanly
- Log significant actions in task.yaml `work_log`

### execute → document

1. Ensure all changes committed → **git-mastery**: `git status` clean
2. Update task.yaml work_log with summary of what was done

### document

Update documentation affected by changes.

- README, CHANGELOG, inline docs — anything users or future agents read
- If no documentation changes needed — transition immediately to `review`
- Keep docs minimal and accurate — don't over-document

### document → review

1. Verify docs reflect the actual changes (not stale)
2. Commit documentation changes → **git-mastery**

### review

Analyze quality of work done.

- Run **self-retrospective** if there were problems (failures, backtracks, wasted iterations)
- Update task card with final state description
- Create improvement task cards from retrospective findings (if any)
- Transition to `done` when acceptance criteria met

### review → done

1. Run **task-summary**: capture architectural decisions, decision forks, and pitfalls
2. Merge worktree back → **worktree-isolation**: `ai-hats wt merge`
3. Update task.yaml: `state: done`, `completed_at: <timestamp>`
4. Update STATE.md

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

## Bundled Rules

### Backlog Discipline
1. **Work Log Cadence**: Update task.yaml work_log after every significant action.
2. **State Transitions**: Update task.yaml state immediately when work changes phase.
3. **STATE.md Sync**: After any task state change, update .agent/STATE.md.
4. **Completion Gate**: Task not done until: state is done, work_log has final entry, STATE.md updated.

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
- Silently skipping user-mentioned approaches — every approach must be explicitly addressed
