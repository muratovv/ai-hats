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

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, resolve the binary once:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> "$AH" task list
> ```
> If neither works, the project's venv lives at `./.venv/bin/ai-hats`. Never call bare `ai-hats` blindly and fall back — it wastes a turn.

> **Note:** Task ID prefix is project-specific (e.g. `PROX-`, `INFRA-`). Examples below use `PROJ-` as a placeholder.

### Task ID prefix

The default prefix is `TASK-` for new projects. Legacy projects with existing
`HATS-*`/`FOO-*` folders get their prefix auto-detected on the first
`ai-hats task create` and persisted to `ai-hats.yaml`.

**Set a custom prefix** when the project tracks work under a specific key
(corporate tag, Jira/Linear project id, etc.):

```bash
# At init time — preferred
ai-hats init -p claude --task-prefix ACME

# Or edit ai-hats.yaml directly
#   task_prefix: ACME
```

Prefix must match `^[A-Z][A-Z0-9]*$` (uppercase, starts with a letter).
Re-running `ai-hats init --task-prefix X` against a project with a different
prefix already in the yaml fails loud rather than silently reassigning ids.

```bash
# Create task (ID auto-generated if omitted)
ai-hats task create "Title" -d "Description" -p medium --tag dx --tag cleanup --id PROJ-042

# Show task
ai-hats task show PROJ-042

# Transition state
ai-hats task transition PROJ-042 plan
ai-hats task transition PROJ-042 execute
ai-hats task transition PROJ-042 done

# Update task fields
ai-hats task update PROJ-042 -p high
ai-hats task update PROJ-042 --description "New description" --resolution "Closed: duplicate"
ai-hats task update PROJ-042 --add-tag refactor --remove-tag wip

# Log work progress
ai-hats task log PROJ-042 "Implemented X, tests green"

# List open tasks (done/failed hidden by default)
ai-hats task list

# All tasks including done
ai-hats task list --all

# Filter by state or priority
ai-hats task list --state brainstorm --priority high

# Search by regex across id, title, description, tags, parent_task
ai-hats task list --search epic              # find epics
ai-hats task list --search PROJ-092          # epic + children (via parent_task)
ai-hats task list --search "judge|retro"     # regex OR

# Sync STATE.md and backlog.md
ai-hats task sync
```

> **CLI-only enforcement** is owned by rule **dev_rule_backlog_discipline**. Never access `.agent/backlog/tasks/` directly — use the CLI commands above.

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
- **Integration tagging:** decide whether the task involves integration with an
  external tool, process, network call, sub-agent, or filesystem writes outside
  `.agent/`. If yes → `ai-hats task update <ID> --add-tag integration`. This
  activates the pre-commit smoke gate (owned by **git-mastery**), which runs
  `pytest -m smoke` on every commit throughout the task lifecycle.
- Output: task.yaml with clear description and acceptance criteria
- Transition: `ai-hats task transition <ID> plan` when scope is understood

### plan

Draft an implementation plan. Attach to task directory as `plan.md`.

- **Approach validation (before elaborating):** Describe the proposed approach
  in 2-3 sentences — core idea, key trade-off, alternative considered.
  Wait for user confirmation before writing the full plan.
  Do NOT elaborate implementation details, component breakdowns, or
  state machines until the user confirms the direction.
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
1. `ai-hats wt create type/TICKET-ID` (e.g. `feat/PROJ-004`) → **worktree-isolation**
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

## Session Scoping

After closing **2 or more tasks** in a single session, suggest wrapping up and
starting a new session. This preserves session-level granularity for
retrospective analysis and reduces blast radius of context drift.

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
- Silently skipping user-mentioned approaches — every approach must be explicitly addressed
