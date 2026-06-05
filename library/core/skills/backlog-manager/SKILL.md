---
name: backlog-manager
description: Backlog lifecycle orchestration for tasks, hypotheses, and proposals via the ai-hats CLI. Use when starting any new task, hypothesis, or proposal, managing state transitions on any of the three types, coordinating sub-agent delegation, or recording verdicts on hypotheses or votes on proposals.
---
# Backlog Manager

Orchestrate the lifecycle of all three backlog item types via the `ai-hats task` CLI:

| Type | ID prefix | YAML location | State machine |
|---|---|---|---|
| **Task** | `<PROJ>-NNN` (e.g. `HATS-NNN`) | `<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml` | `brainstorm → plan → execute → document → review → done` (+ `blocked`, `failed`, `cancelled`) |
| **Hypothesis** | `HYP-NNN` | `<ai_hats_dir>/tracker/hypotheses/HYP-NNN.yaml` | `active → confirmed | refuted | stalled` |
| **Proposal** | `PROP-NNN` | `<ai_hats_dir>/tracker/backlog/proposals/PROP-NNN.yaml` | `open → accepted | rejected | deferred | duplicate` |

CLI-only enforcement is owned by rule **rule_backlog_discipline**: never read or edit `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly — always through the verbs documented below.

This SKILL.md is the **index** (overview, core task CLI, FSM, state→skill routing); per-domain detail lives one level deep in `references/` — pull it only when you work in that domain.

## When to Use
- Starting any new task, hypothesis, or proposal
- Managing state transitions on any of the three types
- Coordinating sub-agent delegation
- Recording verdicts on hypotheses or votes on proposals

## CLI Interface

**All backlog operations MUST use the `ai-hats task` CLI. Never create task directories or YAML files manually.**

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, resolve the binary once:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> "$AH" task list
> ```
> If neither works, the project's venv lives at `./.venv/bin/ai-hats`. Resolve the binary path explicitly — falling back blindly between `ai-hats` and the venv path wastes a turn.

> **Run from the main repo, never a linked worktree.** The tracker
> (`<ai_hats_dir>`, under the gitignored `.agent/`) is NOT version-controlled,
> so a freshly-created worktree carries an empty tracker — its id counters
> restart at `001` and collide with the real backlog. Run EVERY backlog CLI
> write — `task create`/`transition`, `hyp create`, `proposal create` — from
> the main repo. From inside a worktree they either fail (`task not found`) or
> silently write to a throwaway tracker that never merges. The "edit work
> happens in the worktree" rule applies to git-tracked source only.

> **Note:** Task ID prefix is project-specific (e.g. `PROX-`, `INFRA-`). Examples below use `PROJ-` as a placeholder. Custom-prefix setup → `references/lifecycle.md`.

```bash
# Create task (ID auto-generated if omitted)
ai-hats task create "Title" -d "Description" -p medium --tag dx --tag cleanup --id PROJ-042

# Show task
ai-hats task show PROJ-042

# Transition state
ai-hats task transition PROJ-042 plan
ai-hats task transition PROJ-042 execute
ai-hats task transition PROJ-042 done

# Cancel — terminal admin-close from any non-terminal state (won't-fix / duplicate / obsolete).
# --resolution is required: it's the audit trail for why the task was dropped.
ai-hats task transition PROJ-042 cancelled --resolution "duplicate of PROJ-040"

# Update task fields
ai-hats task update PROJ-042 -p high
ai-hats task update PROJ-042 --description "New description" --resolution "Closed: duplicate"
ai-hats task update PROJ-042 --add-tag refactor --remove-tag wip

# Log work progress
ai-hats task log PROJ-042 "Implemented X, tests green"

# List open tasks (done/failed hidden by default)
ai-hats task list
ai-hats task list --all                       # include done
ai-hats task list --state brainstorm --priority high

# Search by regex across id, title, description, tags, parent_task, depends_on
ai-hats task list --search epic              # find epics
ai-hats task list --search PROJ-092          # epic + children + anything that depends on PROJ-092
ai-hats task list --search "docs|retro"      # regex OR

# Sync STATE.md
ai-hats task sync
```

> **CLI-only enforcement** is owned by rule **rule_backlog_discipline**. Never access `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly — use the CLI commands above.

## State Machine

```
brainstorm → plan → execute → document → review → done
               ↕       ↕         ↕          ↕
            blocked  blocked   blocked    failed → brainstorm

           any non-terminal state ──────────────→ cancelled  (terminal)
```

`done` and `cancelled` are both terminal. `done` = work completed. `cancelled` =
administratively closed (won't-fix, duplicate, obsolete). Keep them separate so
"closed in sprint" reports don't conflate completed work with discarded work.

## Lifecycle: state → skill routing

Each state hands off to the skill that owns its quality gate. Full per-state
procedure, the two plan→execute flows, and edge-case gotchas →
`references/lifecycle.md`.

| State | Invoke | Key CLI |
|---|---|---|
| **brainstorm** | requirements-interview, request-supervisor | `task create` → `transition plan` |
| **plan** | plan-discipline (authoring), context-handoff | `transition execute` |
| **execute** | scope-guard, git-mastery, request-supervisor, context-reset | `task log`; commit each checkpoint |
| **document** | — | `transition review` |
| **review** | self-retrospective, task-summary | `transition done` |
| **review → done** | task-summary, worktree-isolation | `wt merge`; `transition done`; `task sync` |
| **failed** | self-retrospective, worktree-isolation | `transition brainstorm` |
| **blocked** | request-supervisor | `transition blocked` |
| **cancelled** | — | `transition cancelled --resolution "<why>"` |

## References

One level deep — pull only the domain you're working in:

- `references/lifecycle.md` — full per-state procedure, plan→execute flows, task-ID prefix setup, session scoping.
- `references/hypotheses.md` — `task hyp …`: field contract, good/bad examples, CLI, discovery/closing flows.
- `references/proposals.md` — `task proposal …`: CLI + status semantics.
- `references/attachments.md` — `task attach …`: CLI, idempotency, pre-commit guard, digest.
- `references/relationships.md` — `parent_task` vs `depends_on`: intent, CLI, validation behavior.

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
- Silently skipping user-mentioned approaches — every approach must be explicitly addressed
