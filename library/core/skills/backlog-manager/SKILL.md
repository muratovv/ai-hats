---
name: backlog-manager
description: Backlog lifecycle orchestration for tasks, hypotheses, and proposals via the ai-hats CLI
---
# Backlog Manager

Orchestrate the lifecycle of all three backlog item types via the `ai-hats task` CLI:

| Type | ID prefix | YAML location | State machine |
|---|---|---|---|
| **Task** | `<PROJ>-NNN` (e.g. `HATS-NNN`) | `<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml` | `brainstorm → plan → execute → document → review → done` (+ `blocked`, `failed`, `cancelled`) |
| **Hypothesis** | `HYP-NNN` | `<ai_hats_dir>/tracker/hypotheses/HYP-NNN.yaml` | `active → confirmed | refuted | stalled` |
| **Proposal** | `PROP-NNN` | `<ai_hats_dir>/tracker/backlog/proposals/PROP-NNN.yaml` | `open → accepted | rejected | deferred | duplicate` |

CLI-only enforcement is owned by rule **rule_backlog_discipline**: never read or edit `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly — always through the verbs documented below.

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

> **Note:** Task ID prefix is project-specific (e.g. `PROX-`, `INFRA-`). Examples below use `PROJ-` as a placeholder.

### Task ID prefix

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

# All tasks including done
ai-hats task list --all

# Filter by state or priority
ai-hats task list --state brainstorm --priority high

# Search by regex across id, title, description, tags, parent_task, depends_on
ai-hats task list --search epic              # find epics
ai-hats task list --search PROJ-092          # epic + children + anything that depends on PROJ-092
ai-hats task list --search "docs|retro"      # regex OR

# Sync STATE.md
ai-hats task sync
```

> **CLI-only enforcement** is owned by rule **rule_backlog_discipline**. Never access `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly — use the CLI commands above.

## Hypotheses (`ai-hats task hyp …`)

Hypotheses (HYP-NNN) are proposed changes with measurable expectations. Verdicts come from `reflect-session` runs on session retros. Status flips manually via CLI after the validation window closes.

### Field contract (read before authoring)

The four creation fields carry distinct semantic loads. Mixing them up
produces HYPs that get closed `refuted` because the wrong thing was
being tested. The audit of HYP-001..026 found ~50% of HYPs filed with
fields confused or redundant.

| Field | Carries | NOT |
|---|---|---|
| `--title` | The **class** of behaviour (broad enough to cover adjacent flavors). | A single-incident description. |
| `--hypothesis` | The **mechanism** — *why* the agent does X. The CAUSE from self-retro 4.5.b. | A restatement of the observation. |
| `--baseline` | The **observation** — concrete cited symptoms from sessions. The WHAT WENT WRONG trace. | A re-paraphrase of the hypothesis. |
| `--success-criterion` | How a future auditor decides confirmed/refuted, in countable terms. | A wish ("agent improves"). |

Sanity check: read `hypothesis` and `baseline` side by side. If they
say the same thing in different words, the mechanism wasn't reached —
walk one more "why?" via self-retro 4.5.b before filing.

#### ✓ Good: class title + mechanism hypothesis (HYP-022)

- `title`: "agent re-emits same class of shell-quoting bug across
  adjacent flavors despite knowing one — does not adopt minimum-risk
  heredoc form by default" — names a **class**, not one incident.
- `hypothesis`: "agent knows ONE specific shell-quoting hazard and
  fixes that one, but adjacent flavors (nested EOF, dollar-sign
  interpolation, glob expansion) recur because the agent doesn't
  generalize to the minimum-risk form: unique heredoc marker that does
  not appear in the body, single-quoted to disable all interpolation" —
  this is a **mechanism** (one-shot lesson instead of class
  discipline), not a paraphrase of the symptoms.
- `baseline`: concrete trace — "Library-curation pass 1 session: hit
  shell-quoting bug twice. (1) HATS-528 task create with double-quoted
  heredoc + backtick code fences → zsh cmd-sub evaluated YAML lines.
  (2) Same session, HATS-537 with single-quoted heredoc but nested
  example EOF terminator closed outer heredoc early. ~10 min recovery."

#### ✗ Bad: thin "assumes X" passed off as mechanism (HYP-023)

- `title`: "Agent manually merges task branch before FSM transition,
  triggering double-merge conflict" — describes ONE incident, not a
  class.
- `hypothesis`: "Agent assumes `task transition done` requires the task
  branch to be pre-merged to master" — sounds like a mechanism but is
  surface. The HYP closed `refuted` because the real mechanism was
  muscle-memory git workflow reflex, not an articulated assumption
  about FSM contracts.
- **Correct response:** walk 5 Whys further — *why* would the agent
  assume that? Answer: pre-FSM git workflow reflex (`merge --no-ff`
  + push is the universal "ship a feature branch" pattern; FSM
  internalisation is shallow). That mechanism, framed as a class,
  would have covered every FSM-vs-git-reflex collision, not just the
  pre-merge case.

```bash
# Create a new hypothesis (auto-id, status=active)
ai-hats task hyp create \
  --title "Short title" \
  --hypothesis "<expected improvement statement>" \
  --source-task PROJ-042 \
  --baseline "<measurable starting state>" \
  --expected-outcome "<bullet 1>" --expected-outcome "<bullet 2>" \
  --observation-window "4 sessions" \
  --success-criterion "<how a verdict is decided>"

# List
ai-hats task hyp list                         # all
ai-hats task hyp list --status active --json  # filter

# Show
ai-hats task hyp show HYP-001

# Append a verdict (atomic, filelock-protected)
ai-hats task hyp append-verdict \
  --hyp HYP-001 --session <SID> \
  --verdict {confirmed|refuted|inconclusive|n/a} \
  --evidence "<one-line citation>" \
  --recommendation {close_confirmed|close_refuted|keep|extend_window}

# Flip status (after the validation window closes)
ai-hats task hyp set-status --hyp HYP-001 --status confirmed
ai-hats task hyp set-status --hyp HYP-001 --status refuted
ai-hats task hyp set-status --hyp HYP-001 --status stalled

# One-shot normalize all HYP-*.yaml under current schema (idempotent)
ai-hats task hyp migrate [--dry-run]
```

**Discovery flow** — when to create:
- After spotting a recurring pattern across session retros (≥3 sessions where the same friction shows up).
- After a fix lands and you want to validate it.
- Always pair with a `--source-task` so the change is traceable.

**Closing flow** — when to flip status:
- After the validation window declared on creation has filled (`--observation-window`).
- Final `append-verdict` carries a terminal `--recommendation` (`close_confirmed` / `close_refuted`).
- Then `hyp set-status` flips `active → confirmed | refuted | stalled` (the verdict CLI does NOT auto-flip status).

**Cross-project hypotheses:** before parking a hypothesis as "untestable from this repo" or starting a retirement clock for lack of evidence, ASK the user whether sibling projects exist where the hypothesis is testable. Do NOT auto-survey other directories without confirmation.

## Proposals (`ai-hats task proposal …`)

Proposals (PROP-NNN) are improvement suggestions emitted by reflect-session or other reviewers. Status regulates visibility — accepted/rejected/deferred proposals stay on disk for traceability.

```bash
# Create
ai-hats task proposal create \
  --title "<title>" \
  --category {rule|skill|code|process|doc} \
  --target "<rule/skill/file/process name>" \
  --description "<what>" \
  --rationale "<why>" \
  --related-hypotheses HYP-001,HYP-005 \
  --session <SID>

# List
ai-hats task proposal list --status open --json
ai-hats task proposal list --category rule

# Show
ai-hats task proposal show PROP-001

# +1 vote
ai-hats task proposal vote --prop PROP-001 --session <SID> --reasoning "agree"

# Status
ai-hats task proposal status --prop PROP-001 --status accepted
```

## Attachments (`ai-hats task attach …`)

Attach files (plans, diagrams, sample inputs) to a task card. Blob lives in
`<ai_hats_dir>/tracker/backlog/tasks/<ID>/attachments/<name>`; the manifest
entry (`name`, `digest`, `added`, `note`) is stored in `task.yaml::attachments[]`.
Works on tasks in **any** state, including `done` / `cancelled` — postmortem
artifacts and late corrections are intentional.

```bash
# Move a file into the task's attachments/ and record it in the manifest.
# Idempotent: re-running with identical content is a no-op.
ai-hats task attach add PROJ-042 /path/to/plan.md --note "design draft"

# Override the attachment name (default: basename)
ai-hats task attach add PROJ-042 /tmp/abc.tmp --name diagram.d2

# List
ai-hats task attach list PROJ-042

# Print content to stdout (binaries print only the path + warning)
ai-hats task attach show PROJ-042 plan.md

# Detach (also deletes the blob)
#   tracked blob → silent remove (recoverable via 'git restore')
#   untracked   → requires --yes (deletion is permanent)
ai-hats task attach remove PROJ-042 plan.md
ai-hats task attach remove PROJ-042 plan.md --yes
```

**Idempotency & conflicts.** `attach add` is intentionally idempotent for the
common case — re-attaching the same file with the same name is a no-op. But
attaching **different** content under an **existing** name is a hard error,
not a silent overwrite. To replace: `attach remove` first, then `attach add`.

**Pre-commit guard (HATS-402).** A pre-commit hook installed by this skill
blocks commits that add or modify files under `tasks/<ID>/attachments/`
without a matching manifest entry. The fix is always the same: run
`attach add` to register the file. Per-commit override:
`AI_HATS_ATTACH_ACK=1 git commit …`.

**Digest.** The recorded `digest` is the first 12 hex chars of the blob's
SHA-256 — full hash would balloon `task.yaml` and waste agent context on
every read. 48 bits gives a birthday-safe namespace of ~2^24 attachments
per task, well beyond any realistic scale.

## Relationships: parent_task vs depends_on

Two distinct relationship types — pick by intent, don't conflate:

- **`parent_task`** (single, scalar) — *composition*. The task is a child of an epic
  or a sub-step of a larger work item. A task has at most one parent. Use this
  to model `epic → tickets` hierarchies.
- **`depends_on`** (list, multiple) — *blocking*. The task cannot meaningfully
  start (or complete) until each listed task is `done`. Use this for ordering
  constraints between peers.

Both live in the YAML card as first-class fields — **do NOT** stuff
"Parent: PROJ-X" / "Depends on: PROJ-Y" lines into the description as
free text. That worked historically but is invisible to CLI filters,
not validated, and breaks on typos.

```bash
# Create with both relationships
ai-hats task create "Implement export pipeline" \
  --id PROJ-110 \
  --parent-task PROJ-100 \
  --depends-on PROJ-105 --depends-on PROJ-107

# Set / change parent later
ai-hats task update PROJ-110 --parent-task PROJ-101
ai-hats task update PROJ-110 --clear-parent

# Mutate blockers later
ai-hats task update PROJ-110 --add-depends PROJ-108 --remove-depends PROJ-105

# Inspect — `task show` resolves depends_on to a "Blocked by:" section
# with each blocker's current state, so you can see at a glance what's
# still unblocking the task.
ai-hats task show PROJ-110

# Find everything that depends on PROJ-105 (regex search covers depends_on too)
ai-hats task list --search PROJ-105
```

**Validation behavior:**
- Self-references (parent or depends pointing at the same task) → hard error.
- Immediate two-task cycles (`A.depends=[B]` and `B.depends=[A]`) → hard error.
  Deeper transitive cycles are not detected — keep the dependency graph shallow.
- Unknown reference IDs → **warning** (yellow), but the write succeeds. This
  allows forward-references during planning and lets you fix typos with a
  follow-up `task update`.

## Task Card

Each task gets a directory: `<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml` + artifacts.

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

---

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
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md` — the empty scaffold
  created by `transition <ID> plan`. Use the Write or Edit tool. The
  tracker `plan.md` is the canonical location; no sync step is needed.
  `transition <ID> execute` is blocked until `plan.md` is no longer the
  empty scaffold. Example end-to-end:
  ```
  ai-hats task transition HATS-NNN plan        # scaffolds tracker plan.md
  # Write tool → .agent/ai-hats/tracker/backlog/tasks/HATS-NNN/plan.md
  ai-hats task transition HATS-NNN execute     # reads plan.md, advances FSM
  ```
  **Editable-scratch alternative (opt-in).** If the plan needs heavy
  cross-session iteration before settling, write to
  `<project>/.claude/plans/<NN>-<slug>.md` (or `<prefix-lower>-<NN>-<slug>.md`)
  and let `transition <ID> plan` auto-pick the matching file into the tracker.
  For ambiguous matches or unconventional paths, use
  `ai-hats task plan-sync <ID> [--from-file <path>]`. Choose this route only
  when the cross-session scratch is the actual goal; otherwise direct write
  to the tracker is shorter and leaves no orphan files.
- **Plan → subtasks:** once the plan has `## Subtasks`, `## Steps`, or numbered
  `### N. …` / `### Phase N: …` headings, run
  `ai-hats task plan-extract <ID>` to surface candidates and create child
  tasks in one pass (interactive y/n/edit, or `--auto` / `--dry-run` /
  `--json`). Marker `<!-- <prefix>-NNN -->` makes re-runs idempotent.
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

## Anti-Patterns
- Skipping states — each transition must be explicit, no brainstorm→execute jumps
- Working without a task card — all work must be tracked
- Forgetting work_log updates — the card becomes useless for handover
- Silently skipping user-mentioned approaches — every approach must be explicitly addressed
