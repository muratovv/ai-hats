# Task lifecycle & setup reference

Full per-state procedure for the task FSM, plus task-ID prefix setup and
session scoping. The compact state→skill routing table and the FSM diagram
live in `SKILL.md`; this file is the detail you reach for when you hit a
specific transition or edge case.

## Task ID prefix

The default prefix is `TASK-` for new projects. Legacy projects with existing
`HATS-*`/`FOO-*` folders get their prefix auto-detected on the first
`ai-hats task create` and persisted to `ai-hats.yaml`.

**Set a custom prefix** when the project tracks work under a specific key
(corporate tag, Jira/Linear project id, etc.):

```bash
# At init time — preferred
ai-hats self init -p claude --task-prefix ACME

# Or edit ai-hats.yaml directly
#   task_prefix: ACME
```

Prefix must match `^[A-Z][A-Z0-9]*$` (uppercase, starts with a letter).
Re-running `ai-hats self init --task-prefix X` against a project with a different
prefix already in the yaml fails loud rather than silently reassigning ids.

## Task Card

Each task gets a directory: `<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml` + artifacts.

## States & Transitions

### brainstorm

Create or refine the task card. Clarify requirements.

- Create task: `ai-hats task create "Title" -d "Description" -p <priority>`
- If the request is short or you're making >2 independent assumptions about user intent
  → **requirements-interview**: walk through the structured Q&A before transitioning to plan
- If requirements remain unclear after the interview → **request-supervisor**: ask supervisor for context
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
- **Plan file location:** write plans **directly into**
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md` (the scaffold from
  `transition <ID> plan`); `transition execute` is blocked until it is non-empty.
  The full draft→tracker procedure and the `.claude/plans` ban live in skill
  **plan-discipline** — that is the canonical authoring flow.
- **Plan → subtasks:** once the plan has `## Subtasks`, `## Steps`, or numbered
  `### N. …` / `### Phase N: …` headings, run
  `ai-hats task plan-extract <ID>` to surface candidates and create child
  tasks in one pass (interactive y/n/edit, or `--auto` / `--dry-run` /
  `--json`). Marker `<!-- <prefix>-NNN -->` makes re-runs idempotent.
  Slice shape (one-session tracer bullets, `depends_on` edges, expand–contract
  for wide refactors) → skill **task-slicing**. Same recipe carries a
  mid-execute tail: append it as `## Steps` items, re-run `plan-extract`.
- Transition: `ai-hats task transition <ID> execute` when plan is ready AND all approaches are addressed

### plan → execute

Two equivalent flows — pick one, do not mix:

**A. Auto worktree (single command, branch named `task/<id>`):**

1. `ai-hats task transition <ID> execute` — creates a `task/<id>` worktree automatically
2. `cd <printed-worktree-path>`

> **Do not** pre-create the `task/<id>` branch (e.g. `git branch task/hats-NNN`) before this transition. The CLI invokes `git worktree add -b task/<id> ...`; if the branch already exists, the command fails and you have to delete the branch and retry. (HATS-375)

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

### cancelled

Terminal state for tasks that are **not going to be done** — won't-fix after
review, duplicate of another ticket, obsolete (feature shipped via different
path or dropped from product scope). Reachable from any non-terminal state, so
admin closures don't have to walk the full plan→execute→done cycle.

- **Mandatory**: `--resolution "<why>"` — without it the CLI rejects the
  transition. The resolution is the audit trail.
- Worktree (if any) is discarded — work is not preserved.
- `ai-hats task transition <ID> cancelled --resolution "won't-fix per HATS-NNN review"`
- vs `done`: `done` = completed; `cancelled` = dropped. Keep them distinct so
  velocity / completion metrics aren't polluted by admin closures.
- vs `blocked`: `blocked` is recoverable ("can't right now"); `cancelled` is
  terminal ("not doing this"). If you want to revisit a cancelled idea, open a
  new ticket — there is no reopen.

## Session Scoping

After closing **2 or more tasks** in a single session, suggest wrapping up and
starting a new session. This preserves session-level granularity for
retrospective analysis and reduces blast radius of context drift.
