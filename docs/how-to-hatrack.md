# How-To: day-to-day backlog (`rack`)

Practical walkthrough of the backlog CLI: the three card types, their
lifecycles, and the everyday commands. Audience: a project author who has
installed ai-hats and wants to drive the backlog from the shell.

## What you are holding

Three names show up around this tool. They name three different layers:

| Name               | Layer                                                      | You meet it as                                 |
| ------------------ | ---------------------------------------------------------- | ---------------------------------------------- |
| **`ai-hats-rack`** | the Python package — the backlog kernel                    | a dependency; `packages/ai-hats-rack/`         |
| **`rack`**         | the CLI it ships                                           | what you type: `rack ls`, `rack transition …`  |
| **`hatrack`**      | the skill that teaches a role to drive `rack` in a session | composed via `trait-agent`; the agent reads it |

This doc is about the middle row. Concept definitions — [1]. State-machine
diagrams — [2]. Full flag reference — `rack --help` and `rack <verb> --help`,
which are authoritative over this page. Engine internals (lock model, subscriber
contract, event registry) — [7].

The shape to keep in mind: **`rack` has five verbs, and only one of them
writes.**

```bash
rack create       # new card
rack ls           # search, or walk the link graph
rack context      # THE read: one card, its links, its document paths
rack transition   # THE write: every mutation, composed under one lock
rack plan-extract # child cards from a plan's Steps section
```

Everything else is an option on `transition`. On the tasks backlog there is no
`update`, no `log`, no `link`, no `close` and no `sync` verb — those are all ops
riding the one mutating call.

---

## Which backlog am I writing to?

When executing `rack` commands or running `ai-hats wait`, the project root and target backlog are resolved via a strict precedence ladder:

| Precedence | Level                       | Resolution Rule                                                                                                                                                                                                                                                                           |
| ---------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1          | Explicit CLI / Env Override | `--tasks-dir <path>` flag or `RACK_TASKS_DIR=<path>` environment variable.                                                                                                                                                                                                                |
| 2          | `AI_HATS_DIR` Override      | Points to `<ai_hats_dir>`. Cards resolve under `<ai_hats_dir>/tracker/backlog/tasks`. If `AI_HATS_PROJECT_DIR` is set and does not match the project directory resolved for the current invocation (walk-up from cwd), `rack` refuses execution with exit code 1 (`foreign_project_pin`). |
| 3          | Walk-up Resolution          | Searches current directory and parent directories for `.agent/` (directory) or `ai-hats.yaml` (file in project root). From inside a linked task worktree (where neither marker is present), resolution hops via gitlink to the main checkout.                                             |

> **Note**: Explicit root resolutions (e.g. `--root <dir>` or cross-project roots registry) target the specified root directly and skip Step 2.\
> **Warning on Residual Leak**: `ai-hats.yaml` is always read from the project root (`project_dir`). Therefore, `AI_HATS_DIR` alone does not provide full project config isolation — complete isolation requires a sandbox project root.

### Executable Sandbox Recipe & Fail-Safe Write Probe

When validating automation in a sandbox copy of a workspace, follow this executable recipe to ensure mutations do not leak into the live backlog.

1. **Copy the tracker into a sandbox directory**:
   ```bash
   mkdir -p /tmp/sandbox/.agent/ai-hats
   cp -r .agent/ai-hats/tracker /tmp/sandbox/.agent/ai-hats/
   ```

2. **Seed a probe card using `RACK_TASKS_DIR`**:
   ```bash
   RACK_TASKS_DIR=/tmp/sandbox/.agent/ai-hats/tracker/backlog/tasks \
     rack create "Sandbox Isolation Probe" --id HATS-9999
   ```

3. **Verify isolation with a fail-safe write probe**:
   Run the command targeting `HATS-9999` (which exists ONLY in the sandbox backlog). **Crucial**: Unset `AI_HATS_SESSION_ID` (`env -u AI_HATS_SESSION_ID`) so single-slot task ownership (HATS-955) does not mask isolation failures.
   ```bash
   env -u AI_HATS_SESSION_ID AI_HATS_DIR=/tmp/sandbox/.agent/ai-hats \
     rack transition HATS-9999 execute --log "isolation test"
   ```
   If isolation holds, `HATS-9999` in the sandbox transitions to `execute`. If isolation leaks to the live project root, the command fails with `unknown_task: HATS-9999` (because `HATS-9999` does not exist in live), safely protecting live data from corruption.

---

## The three card types

| Type       | ID prefix  | On disk                                                           | Purpose                                                            |
| ---------- | ---------- | ----------------------------------------------------------------- | ------------------------------------------------------------------ |
| Task       | `HATS-NNN` | `<ai_hats_dir>/tracker/backlog/tasks/HATS-NNN/` (a **directory**) | Scoped unit of work driven through a fixed lifecycle               |
| Hypothesis | `HYP-NNN`  | its own card dir in the `hypotheses` backlog                      | Claim under validation — accumulates verdicts session over session |
| Proposal   | `PROP-NNN` | its own card dir in the `proposals` backlog                       | Improvement idea pending triage                                    |

HYP and PROP are **sibling backlogs** mounted next to tasks, not special cases
of a task. Each declares its own states, fields and link kinds in its
`backlog.yaml`. Ids route by prefix, so the read verbs reach all three without a
flag: `rack context HYP-009` works exactly like `rack context HATS-042`. Only
the *write* verbs are grouped, under `rack hyp` / `rack proposal`.

> **One rule** — never hand-edit `task.yaml`. A file lock guarantees atomic
> writes, and a direct edit races the reflect loop. Writing **documents** into
> the card directory is different, and encouraged — see [Documents](#documents).

---

## In a session: the `hatrack` skill

You rarely run these commands by hand. A role composed with `trait-agent`
drives the backlog on your behalf in natural language; the skill [6] carries the
verbs, the per-edge policy, and the work-log cadence.

Typical in-session prompts:

> "Open a task for wiring the kubernetes-ops skill into the sre role, parent HATS-200."
>
> "Move HATS-358 to plan and draft a plan.md."
>
> "File a hypothesis: filter regressions correlate with sub-agent refactors. Observation window 4 sessions."

**Lifecycle policy lives in the skill, not here.** When to move a card, what
each edge obliges you to do first, and why an agent must stop at `review` — all
of that is [6]'s per-edge table, rendered against the live FSM. This page is the
CLI contract: *what* the commands do. Read [6] for *when*.

The skill declares the tool it drives: its frontmatter carries
`ai_hats.requires.cli: ai-hats-rack` with probe `rack --help` (ADR-0016). The
dependency is declared, verified at compose time, and warned about when missing
— never auto-installed.

---

## Quick start

### a) Create a task and walk it to `done`

```bash
rack create "wire kubernetes-ops skill into the sre role" \
    --priority high \
    --tag infra --tag composition \
    --parent HATS-200            # optional: the epic this belongs to

# Happy path — never skip a state.
rack transition HATS-NNN plan        # scaffolds plan.md
rack transition HATS-NNN execute     # opens an isolated worktree
rack transition HATS-NNN document
rack transition HATS-NNN review
rack transition HATS-NNN done        # the reviewer drives this one
```

Two of those edges are **consent-gated**, so an agent can neither walk its own
plan into implementation nor land its own branch on master without your
approval: `plan → execute` and **every road into** `done` (HATS-1752 — the
forced close included). A direct `ai-hats wt merge` —
the other road into master — is gated the same way.

Which edges those are is not fixed by the backlog: it is the ROLE's
declaration. The agent trait names them, so a role that does not compose it is
asked nothing, and a role that needs a different surface of consent edits the
topology rather than the guard:

```yaml
composition:
  apps:
    consent_gate:
      rack.transition:
        - at: [plan->execute, ->done]
          consent: true
      wt.merge:
        - at: [pre-merge]
          consent: true
```

`apps.consent_gate` is an external command policy. Its adapter understands the
operation's selectors, while `rack` and `wt` receive ordinary commands and know
nothing about consent. A role without this declaration gets the original
command surface unchanged.

On a surface with runtime hooks the agent simply runs the command and the guard
turns it into a **question in chat**. The answer carries a one-shot ticket to the
session-local command wrapper, good for that card, session, and exact command.
The wrapper consumes it immediately before starting the original executable;
downstream validation failure does not restore authorization for another
attempt. Nothing is typed by the agent — a consent prefix it writes itself is
refused as a self-grant.

An entry under `permissions.allow` that covers the call, such as
`"Bash(rack transition *)"`, can suppress the harness question but cannot bypass
the wrapper. Without a grant or ticket the original command still does not
start. A startup check reports such rules because they break the interactive
handshake.

The question goes up before the plan is read, so a `plan → execute` that then
fails on empty plan sections spent your answer on a move that did not happen —
the next attempt asks again.

Where there is nobody to ask — headless (`claude -p`), cron, or a surface with
no runtime hooks — the compatibility channel is the environment that launches
the session:

```bash
export AI_HATS_CONSENT_ACK=1
```

**One flag covers the whole top-level command.** On `review → done` that means
the FSM edge and the worktree merge inside its teardown. The wrapper strips the
authorization before starting `rack`, so nested effects cannot reinterpret or
reuse it. Every accepted compatibility acknowledgement writes one `hatch` line
to `<git-common-dir>/ai-hats/bypasses.jsonl` before the original command starts.

`AI_HATS_PLAN_ACK` and `AI_HATS_MERGE_ACK` remain legacy compatibility channels;
interactive sessions use the question-backed wrapper path above.

There is no `sync` step — see [STATE.md is reactive](#statemd-is-reactive).

### b) Compose several ops into one call

Ops run in **argv order, under one lock, with a single persist**. Any op that
aborts rolls the whole sequence back, so a half-applied transition is not a
state the store can reach.

```bash
rack transition HATS-042 --state execute \
                         --log "impl started" \
                         --link related:HATS-040 \
                         --set role=implementer
```

This is the idiom to reach for. A state move plus its work-log note plus its
field edit is one command, not four.

### c) Fast-close work that already shipped

The legacy `close` verb is a forced edge to a terminal state. `--force` relaxes
the FSM arrow **only**, and requires a reason, which is journaled:

```bash
rack transition HATS-NNN --state done --force \
    --reason "shipped on master in 1644534, no worktree walk needed"
```

Reserve it for `brainstorm` / `plan` cards the full lifecycle would just
bookkeep. From `execute` onward, walk the states normally.

`--force` does **not** relax consent. Consent is a property of the move, not of
the command — `consent | op --force` — so nothing you add to the command line
switches the question off, and there is no set of "flags we do not ask on" left
to join. The recipe still works; it asks once (HATS-1682).

### d) File a HYP from a session

```bash
rack hyp create "filter regressions correlate with sub-agent refactors" \
    --hypothesis "Every regression in observe.py filters in the past month \
                  followed a SidecarTracer refactor." \
    --observation-window "4 sessions" \
    --success-criterion "zero new filter regressions in the window"

# tie it to the task it came from (a link kind, not a create flag)
rack transition HYP-NNN --link source_task:HATS-029
```

The card lands `active`. Every subsequent `session-reviewer` run appends a
verdict — see [5].

### e) Where PROPs come from

Most proposals are filed automatically by `session-reviewer` when it hits a
self-problem; you triage them with `ai-hats reflect` (see [5]). By hand:

```bash
rack proposal create "pre-commit gate should run filter-specific tests" \
    --category process \
    --target pre-commit \
    --description "..." \
    --rationale "..."
```

---

## Reading the backlog

Two verbs, and the distinction matters: `ls` finds cards, `context` explains
one.

```bash
rack ls                             # the active backlog
rack ls --state execute             # exact state match
rack ls --tag docs                  # exact tag match
rack ls --parent HATS-092           # direct children of an epic
rack ls --grep "worktree"           # case-insensitive substring, title+description
rack ls --grep id:HATS-092          # …or over one field: id, title, description
rack ls --all                       # lift the 30-row cap
```

The first four match a field exactly. `--grep` does not — it is the fallback
for when all you have is a word, so try the others first.

`--grep` is a **literal substring**, not a regex. Bare, it reads title +
description, which is why an id-shaped needle returns the cards *citing* an id
rather than the card that *is* it — `id:` narrows the haystack to one field. A
prefix that is not a field name stays literal, so `--grep "runner.py:42"`
still searches for that text.

```bash
rack ls HATS-092 --deep 2                      # walk 2 link-edges out
rack ls HATS-092 --deep 1 --link parent_task   # …following only one kind
```

`rack context` is the one to reach for when you actually need to work a card —
it returns the full card, its links, and the **paths** of its documents:

```bash
rack context HATS-042
rack context HATS-042 HATS-043        # batch: one process, {"contexts": {…}}
rack context HATS-042 --attr audit    # the dispatch journal
rack context HATS-042 --attr work_log
rack context HATS-042 --with 'plan*'  # embed matching document bodies
```

Every verb takes `--json`.

Cross-project reads work through the roots registry: `rack root add <path>`,
then `rack ls --projects all` or `rack context projB:HATS-9`.

### STATE.md is reactive

`<ai_hats_dir>/STATE.md` is a generated index of active cards. **There is no
`rack sync`** — and nothing to remember. The derived-views extension regenerates
STATE.md post-lock after every write, under its own lock, by atomic replace. The
legacy `sync` verb existed because regeneration was manual; it no longer is.

Agents are not handed STATE.md in their prompt — they read the backlog on demand
via `rack ls` / `rack context`.

---

## Tasks (`HATS-NNN`)

### Lifecycle

Happy path: `brainstorm → plan → execute → document → review → done`.

Side routes: `blocked` (from `brainstorm` / `plan` / `execute` / `document` —
**not** from `review`), `failed` (from `execute` / `review`, recoverable via
`brainstorm`), `cancelled` (from any non-terminal state). From `review` a rework
path returns to `execute` — that is the review loop, and it fires no merge, so
the worktree survives. Two named edges: `reclaim` (`execute → execute`) and
`reopen` (`done → execute`, for finishing epic scope).

An illegal edge is refused with the legal set printed:

```
$ rack transition HATS-001 done
error: Invalid transition for HATS-001: brainstorm → done.
       Legal edges from 'brainstorm': plan, blocked, cancelled
```

Full diagram — [2]. Per-edge obligations — [6].

### `rack create` fields

| Flag                     | Notes                                                    |
| ------------------------ | -------------------------------------------------------- |
| `--description <text>`   | inline description                                       |
| `--priority <p>`         | `low` / `medium` / `high` / `critical`; default `medium` |
| `--tag <t>` (repeatable) | e.g. `--tag docs --tag milestone-1.0`                    |
| `--parent HATS-NNN`      | epic → child; epicifies the parent                       |
| `--depends HATS-NNN`     | blocker                                                  |
| `--reviewer <who>`       | who closes the card                                      |
| `--role <name>`          | suggested role for the executor                          |
| `--work-policy <text>`   | policy inherited by this card **and its children**       |
| `--id <ID>`              | explicit id; default allocates the next                  |

`work_policy` is the one field that travels down the parent chain: `rack context
<child>` delivers every ancestor's policy into the child's read. Put per-stage
child policy there, not in the description.

### Field edits — `--set` and `--append`

There is no `update` verb on the tasks backlog. Field edits are ops on
`transition`, schema-validated on the same lock as a state move — a bad choice
or type is a typed refusal that persists nothing. `--set` replaces a field,
`--append` adds to a list one; a payload is JSON when it parses and plain text
otherwise, and a JSON array adds its **entries**, never itself:

```bash
rack transition HATS-042 --set priority=high --set reviewer=@lead
rack transition HATS-042 --set title="Sharper title"
rack transition HATS-042 --set description="$(cat body.md)"
rack transition HATS-042 --append tags=dx                # one entry
rack transition HATS-042 --append 'tags=["dx","rack"]'   # each entry
rack transition HATS-042 --set 'tags=["dx"]'             # replace the list
rack transition HATS-042 --unlink parent_task:HATS-010 --link parent_task:HATS-014   # re-parent
```

A write that would produce a card the reader cannot load back is refused at the
persist step, so no field op can strand a card behind its own validation.

The `hyp` and `proposal` groups *do* carry an `update` verb for scalar fields;
it maps onto these same `--set` ops.

### Work-log cadence

`--log` appends a `work_log` entry. Append after every significant action:
approach changes, file deletions, branch operations, milestone completions. The
`session-reviewer` reads `work_log` to write the retrospective — thin logs
produce thin retros.

```bash
rack transition HATS-NNN --log "abandoned overlay approach — replacing role wholesale"
```

Log **as you go**, composed onto the transition that earned the note, rather
than as one batch at the end.

### Links

```bash
rack transition HATS-042 --link related:HATS-040
rack transition HATS-042 --link depends_on:HATS-041
rack transition HATS-042 --unlink HATS-040             # kind optional
```

Configured kinds on the tasks backlog: `parent_task`, `depends_on`, `related`,
`see_also`, `folded_into`, and the derived `children` and `blocks`. An unknown
kind is a typed refusal listing the legal set. Cross-backlog kinds
(`source_task` on a HYP, `related_hypotheses` on a PROP) mirror automatically.

A derived kind is read-only — it is the reverse view of a stored one, so you
link the stored side and read the other. `blocks` inverts `depends_on`: mark
what a card waits on, and every card it holds up shows the reverse without a
second edge to keep in sync.

```bash
rack transition HATS-042 --link depends_on:HATS-041
rack context HATS-041                                  # -> Blocks: HATS-042
```

`related` and `see_also` are both symmetric soft pointers — reach for `related`
by default and keep `see_also` for the weaker "worth a look" nod. `folded_into`
is the only directional kind: it records that a card was **subsumed** by
another, for when you find a duplicate after both have history worth keeping.
It takes a single target, and `--link fold:<ID>` is accepted as a spelling.

```bash
rack transition HATS-042 --link folded_into:HATS-040   # 042 was subsumed by 040
```

Cancelling with a `--resolution` string says the same thing in prose;
`folded_into` says it in a field, so the pointer survives as data.

### Documents

The store is **fs-as-truth**: the only way to write a document is to put a file
into the card's directory, `<ai_hats_dir>/tracker/backlog/tasks/<ID>/`. There is
no `put` verb and no manifest to keep in sync. `rack context` live-scans the
directory and digests on the fly, so a file you write with an editor is visible
immediately — no registration, no write-then-register race.

```bash
# "attaching" a file is just getting it into the directory — either works:
cp /tmp/design.md .agent/ai-hats/tracker/backlog/tasks/HATS-042/
rack transition HATS-042 --attach /tmp/design.md:design.md
```

`rack context` prints each document's name, **absolute path**, mtime and frozen
mark. Read it by that path — content is never inlined unless you ask for it with
`--with <glob>`.

**Freezing.** Pinning a document marks it as evidence:

```bash
rack transition HATS-042 --freeze design.md          # pin {name, digest}
rack transition HATS-042 --rm design.md              # trash (recoverable)
```

Once pinned, the frozen-integrity extension aborts **any** transition whose
pinned document changed or vanished — forced edges, epics and automation actors
included; there are no waivers on evidence integrity. The refusal names the
document, both digests, and the recovery recipe. Re-pinning drifted content or
removing a pinned document needs `--ack-frozen`. `--rm` trashes to `$TMPDIR` and
never hard-deletes.

### Splitting a plan into child cards

Once a plan's `## Steps` headings have stabilised:

```bash
rack plan-extract HATS-042
```

It creates one child card per step. Run it when the headings are stable — not
while they are still moving.

---

## Hypotheses (`HYP-NNN`)

### When a HYP, not a task

Task = scoped work with a deliverable. HYP = a claim about the system or your
process that needs evidence across multiple sessions before you act. If you
cannot define "done" but you *can* define "we'd be sure if we saw X over Y
sessions" — that is a HYP.

### Create

```bash
rack hyp create "..." \
    --hypothesis "..." \
    --baseline "3 filter regressions / 4 weeks" \
    --expected-outcome "0 filter regressions for the next 4 weeks" \
    --observation-window "4 sessions" \
    --success-criterion "zero new filter regressions in the window" \
    --rollback-condition "still seeing regressions despite the new tests" \
    --exit-criteria "confirm: 3+ confirmed"
```

The title is **positional**. There is no `--source-task` flag — the origin task
is a link: `rack transition HYP-NNN --link source_task:HATS-029`. Status starts
at `active`. Sample shape — [3].

### Verdicts

Usually written by `session-reviewer` after each session (see [5]). By hand:

```bash
rack hyp append-verdict HYP-NNN \
    --verdict confirmed \
    --evidence "filter test caught the productive_only edge case before merge" \
    --recommendation keep
```

`--session-id` defaults to the ambient `AI_HATS_SESSION_ID`, which is what makes
quorum counting work out of the box. Appending a verdict is atomic and walks
**no edge** — it never changes the HYP's state.

Verdicts: `confirmed | refuted | inconclusive | n/a`. The `n/a` verdict means
the session physically could not test the HYP; it is mirrored into the
SessionReview frontmatter and **not** written into `validation_log`, which keeps
the observation window clean. Recommendations: `close_confirmed |
close_refuted | keep | extend_window`.

### Close

Closing is a **named FSM edge**, not a status flag:

```bash
rack transition HYP-NNN confirm       # active → confirmed
rack transition HYP-NNN refute        # active → refuted  (quorum-gated)
rack transition HYP-NNN stall         # active → stalled
rack transition HYP-NNN revive        # stalled → active
```

`refute` is gated: it requires refuted verdicts from **3 independent sessions**.
That gate is the point — one session's bad day cannot kill a hypothesis. To
sweep every HYP that has reached the quorum:

```bash
rack hyp autoclose --dry-run          # report without writing
rack hyp autoclose --k 3
```

`exit_criteria` on each HYP names the verdict counts that should close it; the
reflect walker reads them alongside the `validation_log`.

---

## Proposals (`PROP-NNN`)

### When a PROP, not a task

PROP = an improvement idea that has not been triaged. If you have already
decided to act — file a task. If the idea needs `+1`s from independent sessions
before it earns a slot — a PROP.

### Create, vote, triage

```bash
rack proposal create "pre-commit gate should run filter-specific tests" \
    --category process \
    --target pre-commit \
    --description "..." \
    --rationale "..."

# Co-sign from a different session
rack proposal vote PROP-NNN --reasoning "saw the same blind spot on this run"

rack ls --backlog proposal --state open
rack context PROP-NNN
```

Like `append-verdict`, `vote` is atomic and walks no edge. Triage moves the card
by named edge:

```bash
rack transition PROP-NNN accept          # open → accepted
rack transition PROP-NNN reject
rack transition PROP-NNN defer
rack transition PROP-NNN mark-duplicate
rack transition PROP-NNN reopen          # deferred → open
```

The routine path is `ai-hats reflect`, which walks every open PROP and takes
your decisions in batch — see [5]. Reach for the edges above only for stray,
off-cycle flips.

---

## References

**[1]** — [`docs/glossary.md`](glossary.md) — naming source-of-truth for ai-hats core terms (task / HYP / PROP / session / reflect).

**[2]** — [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — FSM diagrams for tasks / HYPs / PROPs.

**[3]** — [`tests/fixtures/real_backlog/HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) — synthetic hypothesis with `validation_log`.

**[4]** — [`tests/fixtures/real_backlog/PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml) — synthetic proposal with `votes[]`.

**[5]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — `reflect` workflows: session review, HYP verdicts, PROP triage.

**[6]** — [`ai_hats_library/core/skills/hatrack/SKILL.md`](../packages/ai-hats-library/src/ai_hats_library/core/skills/hatrack/SKILL.md) — in-session skill that drives this CLI on behalf of any role; owns the per-edge lifecycle policy.

**[7]** — [`packages/ai-hats-rack/README.md`](../packages/ai-hats-rack/README.md) — engine reference: transition pipeline, subscriber contract, event registry, lock model, journal.
