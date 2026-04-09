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

## CLI Interface

**All backlog operations MUST use the `ai-hats task` CLI. Never create task directories or YAML files manually.**

```bash
# Create task (ID auto-generated if omitted)
ai-hats task create "Title" -d "Description" -p medium --tag dx --tag cleanup --id HATS-042

# Show task
ai-hats task show HATS-042

# Transition state
ai-hats task transition HATS-042 plan
ai-hats task transition HATS-042 execute
ai-hats task transition HATS-042 done

# Update task fields
ai-hats task update HATS-042 -p high
ai-hats task update HATS-042 --description "New description" --resolution "Closed: duplicate"
ai-hats task update HATS-042 --add-tag refactor --remove-tag wip

# Log work progress
ai-hats task log HATS-042 "Implemented X, tests green"

# List open tasks (done/failed hidden by default)
ai-hats task list

# All tasks including done
ai-hats task list --all

# Filter by state or priority
ai-hats task list --state brainstorm --priority high

# Sync STATE.md and backlog.md
ai-hats task sync
```

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

- Create task: `ai-hats task create "Title" -d "Description" -p <priority>`
- If requirements are unclear → **request-supervisor**: ask supervisor for context
- Output: task.yaml with clear description and acceptance criteria
- Transition: `ai-hats task transition <ID> plan` when scope is understood

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
- Transition: `ai-hats task transition <ID> execute` when plan is ready AND all approaches are addressed

### plan → execute

Two equivalent flows — pick one, do not mix:

**A. Auto worktree (single command, branch named `task/<id>`):**
1. `ai-hats task transition <ID> execute` — creates a `task/<id>` worktree automatically
2. `cd <printed-worktree-path>`

**B. Custom branch name (manual worktree first):**
1. `ai-hats wt create type/TICKET-ID` (e.g. `feat/HATS-004`) → **worktree-isolation**
2. `cd <printed-worktree-path>`
3. `ai-hats task transition <ID> execute` — adopts the worktree you just `cd`'d into (no nesting)

In both flows, work happens in the worktree — main branch stays clean. Do NOT run `wt create` from inside an existing linked worktree (it will be refused).

### execute

Active development in the worktree.

- Check task boundaries before starting → **scope-guard**: verify scope alignment
- **Commit at every checkpoint** (test pass, sub-task done, file done editing) — not "at the end". Worktrees are NOT safe storage; uncommitted work can be destroyed by parallel sessions or cleanup with no recovery. Conventional format → **git-mastery**: `type(scope): description`.
- Before requesting anything from user → **request-supervisor**: run the checklist
- On context pressure → **context-reset**: save state, write handoff, hand off cleanly
- Log significant actions: `ai-hats task log <ID> "message"`

### execute → document

1. Ensure all changes committed → **git-mastery**: `git status` clean
2. Log summary: `ai-hats task log <ID> "summary of what was done"`
3. `ai-hats task transition <ID> document`

### document

Update documentation affected by changes.

- README, CHANGELOG, inline docs — anything users or future agents read
- If no documentation changes needed — transition immediately to `review`
- Keep docs minimal and accurate — don't over-document

### document → review

1. Verify docs reflect the actual changes (not stale)
2. Commit documentation changes → **git-mastery**
3. `ai-hats task transition <ID> review`

### review

Analyze quality of work done.

- Run **self-retrospective** if there were problems (failures, backtracks, wasted iterations)
- Update task card with final state description
- Create improvement task cards from retrospective findings (if any)
- Transition to `done` when acceptance criteria met

### review → done

1. Run **task-summary**: capture architectural decisions, decision forks, and pitfalls
2. Merge worktree back → **worktree-isolation**: `ai-hats wt merge`
3. `ai-hats task transition <ID> done`
4. `ai-hats task sync`

### failed

Task cannot be completed from execute or review.

- **self-retrospective**: mandatory — analyze why it failed
- Worktree: keep for analysis or discard → **worktree-isolation**: `ai-hats wt discard`
- `ai-hats task log <ID> "failure reason and lessons learned"`
- `ai-hats task transition <ID> brainstorm`

### blocked

Task is blocked by external dependency from any active state.

- **request-supervisor**: document what blocks and request from supervisor
- `ai-hats task log <ID> "blocked: <reason>"`
- `ai-hats task transition <ID> blocked`
- Transition back to previous state when unblocked

## Bundled Rules

### Backlog Discipline
1. **CLI Only**: All task operations go through `ai-hats task` CLI. Never edit task.yaml manually.
2. **Work Log Cadence**: `ai-hats task log <ID> "message"` after every significant action.
3. **State Transitions**: `ai-hats task transition <ID> <state>` immediately when work changes phase.
4. **STATE.md Sync**: Run `ai-hats task sync` after task state changes.
5. **Completion Gate**: Task not done until: state is done, work_log has final entry, sync is run.

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
- Silently skipping user-mentioned approaches — every approach must be explicitly addressed
