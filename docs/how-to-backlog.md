# How-To: day-to-day backlog (`ai-hats task` / `task hyp` / `task proposal`)

Practical walkthrough of the `ai-hats task` CLI: the three backlog item types, their lifecycles, and the everyday commands. Audience: a project author who has installed ai-hats and wants to drive the backlog from the shell instead of memorising `ai-hats --tree task`.

> Full CLI reference with all flags — `ai-hats --tree task`. Concept definitions — [13]. State-machine diagrams — [3]. This doc — recipes.

---

## The three item types

| Type | ID prefix | On disk | Purpose |
|---|---|---|---|
| Task | `HATS-NNN` | `<ai_hats_dir>/tracker/backlog/tasks/HATS-NNN/` (with `task.yaml` + optional `plan.md`) | Scoped unit of work driven through a fixed lifecycle |
| Hypothesis | `HYP-NNN` | `<ai_hats_dir>/tracker/hypotheses/HYP-NNN.yaml` | Claim under validation — accumulates verdicts session over session |
| Proposal | `PROP-NNN` | `<ai_hats_dir>/tracker/backlog/proposals/PROP-NNN.yaml` | Improvement idea pending triage in `reflect all` |

> **One rule** — all three are managed exclusively through `ai-hats task ...`. Never hand-edit the YAML; a file lock guarantees atomic writes, and direct edits will race the reflect loop.

Sample artifacts: [5], [6].

---

## Quick start

Four recipes for the most common day-to-day moves.

### a) Create a task and walk it to `done`

```bash
ai-hats task create "wire kubernetes-ops skill into the sre role" \
    --priority high \
    --tag infra --tag composition \
    --parent-task HATS-200       # optional: epic this belongs to

# Happy path — never skip a state.
ai-hats task transition HATS-NNN plan          # creates plan.md scaffold
ai-hats task transition HATS-NNN execute       # opens an isolated worktree
ai-hats task log HATS-NNN "merged into sre overlay; bumped CLAUDE.md"
ai-hats task transition HATS-NNN document
ai-hats task transition HATS-NNN review
ai-hats task transition HATS-NNN done
ai-hats task sync                              # refresh STATE.md
```

### b) Fast-close work that already shipped on master

```bash
ai-hats task close HATS-NNN \
    --resolution "shipped on master in 1644534, no worktree walk needed"
```

`task close` is reserved for `brainstorm` / `plan` cards that the full lifecycle would just bookkeep. From `execute` onward — walk the states normally.

### c) File a HYP from a session

You spot a recurring pattern worth tracking. Don't open the YAML — ask the running agent, or run the CLI yourself:

```bash
ai-hats task hyp create \
    --title "filter regressions correlate with sub-agent refactors" \
    --hypothesis "Every regression in observe.py filters in the past month \
                  followed a SidecarTracer refactor." \
    --source-task HATS-029 \
    --observation-window "4 sessions" \
    --success-criterion "zero new filter regressions in the window"
```

The card lands `active`. Every subsequent `session-reviewer` run appends a verdict — see [1] for the auto-flow.

### d) Where PROPs come from

Most proposals are filed automatically by `session-reviewer` when it hits a self-problem. You triage them in batch with `ai-hats reflect all` (see [1]). The CLI for filing one by hand:

```bash
ai-hats task proposal create \
    --title "pre-commit gate should run filter-specific tests" \
    --category process \
    --target pre-commit \
    --description "..." \
    --rationale "..."
```

---

## Tasks (`HATS-NNN`)

### Lifecycle

Happy path: `brainstorm → plan → execute → document → review → done`. Side routes: `blocked` (returnable to `plan` or `execute`), `failed` (recoverable via `brainstorm`), `cancelled` (administrative close from any non-terminal state); from `done` a reopen path to `execute` is available for finishing epic scope. Full diagram — [3].

| Command | When |
|---|---|
| `task transition <id> plan` | requirements clear, ready to design — creates `plan.md` scaffold |
| `task transition <id> execute` | plan approved — opens an isolated worktree on `task/hats-NNN` |
| `task transition <id> document` | code merged, ready to capture decisions / pitfalls |
| `task transition <id> review` | docs done, ready for review (set `--final-state` if review accepts an earlier-stage close) |
| `task transition <id> done` | reviewer approved |
| `task transition <id> blocked` | external dependency holding work |
| `task transition <id> cancelled --resolution "..."` | abandoning the work; resolution is mandatory |

### `task create` fields

| Flag | Notes |
|---|---|
| `--priority {low,medium,high}` | default `medium` |
| `--tag` (repeatable) | e.g. `--tag docs --tag milestone-1.0` |
| `--parent-task HATS-NNN` | epic → child relationship |
| `--depends-on HATS-NNN` (repeatable) | blocker — card stays out of `execute` until each blocker hits `done` |
| `--reviewer {user,agent}` | who closes the card |
| `--role <name>` | suggested role for the executor |

### `task log` — work log cadence

Append after every significant action: approach changes, file deletions, branch operations, milestone completions. The `session-reviewer` reads `work_log` to write the retrospective; thin logs produce thin retros.

```bash
ai-hats task log HATS-NNN "abandoned overlay approach — replacing role wholesale instead"
```

### `task sync` — STATE.md

`<ai_hats_dir>/STATE.md` is a generated index of active cards used by every agent at session start. Run `ai-hats task sync` after a batch of state transitions; it's idempotent.

### Ergonomics (HATS-371)

- **Cross-references.** `ai-hats task link FROM TO --type {related|see-also|fold}` — `related` and `see-also` are symmetric soft pointers; `fold` is directional (`FROM.folded_into = TO`) and surfaces as a back-link on TO. `ai-hats task unlink FROM TO --type ...` reverses it. Use `fold` when you discover a duplicate after both cards have history worth preserving.
- **Corrective override.** `ai-hats task transition <id> <state> --force --reason "..."` bypasses the FSM guard and records the reason in `work_log`. Reach for it only when you transitioned to the wrong state; default to the normal path.
- **Fast-close.** `ai-hats task close <id> --resolution "..."` — see Quick start (b).

---

## Hypotheses (`HYP-NNN`)

### When a HYP, not a task

Task = scoped work with a deliverable. HYP = a claim about the system or your process that needs evidence across multiple sessions before you act. If you can't define "done" but you can define "we'd be sure if we saw X over Y sessions" — that's a HYP.

### Create

```bash
ai-hats task hyp create \
    --title "..." \
    --hypothesis "..." \
    --source-task HATS-NNN \
    --baseline "3 filter regressions / 4 weeks" \
    --expected-outcome "0 filter regressions for the next 4 weeks" \
    --observation-window "4 sessions" \
    --success-criterion "zero new filter regressions in the window" \
    --rollback-condition "still seeing regressions despite the new tests"
```

Status starts at `active`. Sample shape — [5].

### Verdicts (`append-verdict`)

Usually written by `session-reviewer` after each session — see [1] for the auto-flow. To add a verdict by hand (e.g. while debugging the loop):

```bash
ai-hats task hyp append-verdict \
    --hyp HYP-NNN \
    --session 20260331-211200-1 \
    --verdict confirmed \
    --evidence "filter test caught the productive_only edge case before merge" \
    --recommendation keep
```

Verdicts: `confirmed | refuted | inconclusive | n/a`. The `n/a` verdict means the session physically couldn't test the HYP and is **not** written into `validation_log` (it only mirrors into the SessionReview frontmatter). Recommendations: `close_confirmed | close_refuted | keep | extend_window` — guidance the next `reflect all` walker reads alongside `exit_criteria`.

### Read / close

```bash
ai-hats task hyp show HYP-NNN          # full validation_log + exit_criteria
ai-hats task hyp list --status active  # backlog snapshot

# Manual close — usually you do this through `reflect all` (see [1]).
ai-hats task hyp set-status --hyp HYP-NNN --status confirmed
```

`exit_criteria` on each HYP names the verdict counts that close it (e.g. `confirm: 3+ confirmed`). `reflect all` reads them per HYP and suggests `confirmed | refuted | stalled`.

---

## Proposals (`PROP-NNN`)

### When a PROP, not a task

PROP = an improvement idea that hasn't been triaged yet. If you've already decided to act — file a task. If the idea needs `+1`s from independent sessions before it earns a slot — a PROP.

### Create / vote / status

```bash
ai-hats task proposal create \
    --title "pre-commit gate should run filter-specific tests" \
    --category process \
    --target pre-commit \
    --description "..." \
    --rationale "..." \
    --related-hypotheses HYP-001

# Co-sign an existing PROP from a different session
ai-hats task proposal vote \
    --prop PROP-NNN \
    --session 20260406-034154-1 \
    --reasoning "saw the same blind spot on this run"

ai-hats task proposal list --status open
ai-hats task proposal show PROP-NNN
```

Sample shape — [6].

### Triage

`ai-hats task proposal status` exists, but the routine path is `ai-hats reflect all`, which walks every open PROP, takes your decisions, and bulk-flips statuses via `ai-hats reflect commit` — see [1]. Use `task proposal status` only for stray, off-cycle flips.

---

## References

**[1]** — [`docs/how-to-feedback-loop.md`](how-to-feedback-loop.md) — `reflect session` + `reflect all` workflows.

**[2]** — [`docs/how-to-configure.md`](how-to-configure.md) — initial setup, role pick, feedback policy.

**[3]** — [`docs/ARCHITECTURE.md#backlog-state-machines`](ARCHITECTURE.md#backlog-state-machines) — FSM diagrams for tasks / HYPs / PROPs.

**[4]** — [`docs/how-to.md`](how-to.md) — `ai-hats.yaml` recipes (overlay, providers, custom skills).

**[5]** — [`tests/fixtures/real_backlog/HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) — synthetic hypothesis with `validation_log`.

**[6]** — [`tests/fixtures/real_backlog/PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml) — synthetic proposal with `votes[]`.

**[13]** — [`docs/glossary.md`](glossary.md) — naming source-of-truth for ai-hats core terms (task / HYP / PROP / session / reflect).

[1]: how-to-feedback-loop.md
[2]: how-to-configure.md
[3]: ARCHITECTURE.md#backlog-state-machines
[4]: how-to.md
[5]: ../tests/fixtures/real_backlog/HYP-001-sample.yaml
[6]: ../tests/fixtures/real_backlog/PROP-001-sample.yaml
[13]: glossary.md
