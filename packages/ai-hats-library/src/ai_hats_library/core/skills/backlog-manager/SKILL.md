---
name: backlog-manager
description: Backlog lifecycle orchestration for tasks, hypotheses, and proposals via the ai-hats CLI. Use when starting any new task, hypothesis, or proposal, managing state transitions on any of the three types, coordinating sub-agent delegation, or recording verdicts on hypotheses or votes on proposals.
ai_hats:
  # Skill-contributed git hooks (HATS-088 framework). The assembler installs
  # these into the project's .githooks/<event>.d/ at composition time.
  git_hooks:
    pre-commit:
      - git_hooks/pre-commit-attachments.sh
  # ADR-0016: this skill drives the ai-hats-tracker `task`/`attach` CLI, so it
  # DECLARES that tool need. Provider-neutral; verified-and-warned (never
  # auto-installed) at compose/session time by the requires verifier (HATS-992).
  requires:
    cli:
      - name: ai-hats-tracker
        check: "ai-hats-tracker --version"
        hint: "pip install ai-hats-tracker"
    mcp: []
license: MIT
---

# Backlog Manager

Orchestrate the lifecycle of all three backlog item types via the `ai-hats task` CLI:

| Type           | ID prefix                      | YAML location                                           | State machine                                                                                 |
| -------------- | ------------------------------ | ------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **Task**       | `<PROJ>-NNN` (e.g. `HATS-NNN`) | `<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml`    | `brainstorm → plan → execute → document → review → done` (+ `blocked`, `failed`, `cancelled`) |
| **Hypothesis** | `HYP-NNN`                      | `<ai_hats_dir>/tracker/hypotheses/HYP-NNN.yaml`         | `active → confirmed \| refuted \| stalled`                                                    |
| **Proposal**   | `PROP-NNN`                     | `<ai_hats_dir>/tracker/backlog/proposals/PROP-NNN.yaml` | `open → accepted \| rejected \| deferred \| duplicate`                                        |

CLI-only enforcement is owned by rule **rule_backlog_discipline**: never read or edit `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly — always through the verbs documented below.

This SKILL.md is the **index** (overview, core task CLI, FSM, state→skill routing); per-domain detail lives one level deep in `references/` — pull it only when you work in that domain.

## When to Use

This is the *full-lifecycle* backlog skill — transitions, hyp/proposal verbs,
work-log cadence, `plan-extract`, sub-agent coordination. Two boundaries:

- **Restricted L1 roles** that may only *file* tasks (no transitions, no
  hyp/proposal mutation) compose **backlog-create** instead — the file-only
  subset.
- Filling a `plan.md`'s required sections at `brainstorm → plan` belongs to
  **plan-gate**; this skill only drives the *state* transitions around it.

## CLI Interface

**All backlog operations MUST use the `ai-hats task` CLI. Never create task directories or YAML files manually.**

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. Before running any `ai-hats` command, define a resolver once (the host launcher on PATH, else the project venv's interpreter — there is no `bin/ai-hats` console script since HATS-790):
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
> ah task list
> ```
>
> If neither works, the project's venv interpreter lives at `./.venv/bin/python` (invoke the package as `./.venv/bin/python -m ai_hats …`). Resolve the path explicitly — falling back blindly wastes a turn.

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

# Markdown/code descriptions ($(...), backticks, nested EOF) → file, not -d
# (`-d "$(cat <<EOF…)"` truncates silently on shell quoting). Mutually exclusive with -d.
ai-hats task create "Title" --description-file /tmp/desc.md -p medium --id PROJ-042

# Show task — by default appends a "Linked context" block with the bodies of
# all linked tasks (parent epic + its plan.md, depends_on/related/see_also),
# the same content a sub-agent gets. Add --short for the compact index only.
ai-hats task show PROJ-042
ai-hats task show PROJ-042 --short

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
ai-hats task update PROJ-042 --description-file /tmp/desc.md   # verbatim; same -d-free safe path
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

           review → execute  (rework loop-back)
           any non-terminal state ──────────────→ cancelled  (terminal)
```

`done` and `cancelled` are both terminal. `done` = work completed. `cancelled` =
administratively closed (won't-fix, duplicate, obsolete). Keep them separate so
"closed in sprint" reports don't conflate completed work with discarded work.

## Lifecycle: state → skill routing

Each state hands off to the skill that owns its quality gate. Full per-state
procedure, the two plan→execute flows, and edge-case gotchas →
`references/lifecycle.md`.

| State             | Invoke                                                      | Key CLI                                     |
| ----------------- | ----------------------------------------------------------- | ------------------------------------------- |
| **brainstorm**    | requirements-interview, request-supervisor                  | `task create` → `transition plan`           |
| **plan**          | plan-discipline (authoring), context-handoff                | `transition execute`                        |
| **execute**       | scope-guard, git-mastery, request-supervisor, context-reset | `task log`; commit each checkpoint          |
| **document**      | —                                                           | `transition review`                         |
| **review**        | self-retrospective, task-summary                            | `transition done`                           |
| **review → done** | task-summary, worktree-isolation                            | `wt merge`; `transition done`; `task sync`  |
| **failed**        | self-retrospective, worktree-isolation                      | `transition brainstorm`                     |
| **blocked**       | request-supervisor                                          | `transition blocked`                        |
| **cancelled**     | —                                                           | `transition cancelled --resolution "<why>"` |

### Pre-execute re-validation (premise freshness)

`rule_backlog_discipline §6` makes a premise retraction a go/no-go event — but a
retraction logged in an *earlier* session does not re-announce itself when a later
agent picks the task up. So this is a **second, independent firing point**: before
`transition <ID> execute`, **mechanically scan** the card for a premise change
since the plan was authored — a `[RETRACTED]` / `[SUPERSEDED]` / *"no longer
holds"* / driver-change marker on the strategic driver or motivating consumer (and
treat a long park between `plan` and pickup as the same signal). Grep the card;
do not eyeball whether it "looks ready". If a marker is unresolved, the
justification is stale: bounce to `brainstorm` so `plan-gate → devils-advocate`
re-fires, or record in the work_log *why* the premise still holds before
proceeding. (Origin: HATS-795 — a retracted driver passed silently into execute
and reached a green ~920-LoC build before cancellation.)

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
