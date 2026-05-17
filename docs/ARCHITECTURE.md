# Architecture

Internal model of ai-hats: components, composition rules, project layout, library structure.

## Component model

| Component | Description           | Format |
|-----------|-----------------------|--------|
| **Rules** | Behavioral directives | `rule.md` + `metadata.yaml` |
| **Skills** | Capabilities with implementation | `SKILL.md` + `metadata.yaml` + `scripts/` + `references/` |
| **Traits** | Composite components | `config.yaml` (composition + injection) |
| **Roles** | Root configurations | `config.yaml` (traits + priorities + injection) |

### Role customization

You can add or remove traits, rules, and skills from a library role without modifying the source config. Customizations live in `ai-hats.yaml` and survive `ai-hats self update` and `ai-hats self bump`.

> A collection of common scenarios with ready-to-use `ai-hats.yaml` examples — see [how-to.md](how-to.md).

```bash
# Add a trait to the sre role
ai-hats config customize sre --add-trait dev::python

# Remove an unneeded skill
ai-hats config customize sre --remove-skill network-documentation

# Append an injection
ai-hats config customize sre --injection-append "Always use k9s for K8s."

# Inspect customizations
ai-hats config customize sre --show

# Apply
ai-hats self bump
```

Format in `ai-hats.yaml`:

```yaml
customizations:
  sre:
    add:
      traits: [dev::python]
      skills: [my-debug-tool]
    remove:
      skills: [network-documentation]
    injection_append: |
      Always use k9s for K8s.
```

Customizations apply on every `config set`, `self bump`, and `--role` override. If `remove` references a component not in the base role — a warning is printed; this is not an error.

### Composition

- Non-commutative — order determines priority (later > earlier)
- Flat — traits do not include other traits (flat model)
- Deduplication — identical injections/rules are not repeated
- Namespaces — `dev::python` → `dev/python` on the FS
- Priorities — only from the root role

#### Composition flow

From role to materialized prompt — a single pipeline; the split happens only at the last step (where the result is delivered):

<p align="center">
  <img src="assets/diagrams/composition-flow.svg" alt="Composition flow diagram" width="520">
</p>

<!-- Source: docs/assets/diagrams/composition-flow.d2 — render: docs/assets/diagrams/render.sh -->


The overlay from `ai-hats.yaml.customizations` affects the pipeline at two points: `add` / `remove` patches the component lists before resolution, and `injection_append` is appended last — after the role's own injection. Deduplication happens during resolution: traits are collected first (depth-first), then the role's own rules and skills are added on top; duplicates by name are ignored.

- **Disk materialization** — the primary path: `ai-hats self bump` writes `CLAUDE.md` / `GEMINI.md` at the project root, and the provider CLI picks them up automatically at session start.
- **In-prompt materialization** — for sub-agents and one-shot roles: the same composition is written to a temporary file and passed via `--system-prompt-file`, without landing in the repo.

### Providers

- **Gemini** — `GEMINI.md` + `GEMINI_CLI_PROJECT_RULES_PATH`
- **Claude** — `CLAUDE.md`

Switching providers: `ai-hats config set -p claude`. The prompt is rebuilt automatically at session start if the provider changed.

## Session lifecycle

When a user runs `ai-hats agent <role>`, the runtime opens an interactive provider-CLI session, writes an incremental trace of every request/response, and closes it via a finalizer. The key idea — **a session implicitly ends with a retrospective** (if auto-retro is enabled and the threshold is met): that's the bridge to the judge cycle described in [Reflection loop](#reflection-loop).

<p align="center">
  <img src="assets/diagrams/session-lifecycle.svg" alt="Session lifecycle diagram" width="420">
</p>

<!-- Source: docs/assets/diagrams/session-lifecycle.d2 — render: d2 --sketch --theme=200 -->


The `Bridge` node — entry into auto reflect-session (see the next section). When `policy=off` or the threshold is not met, the session ends without an LLM call.

**Sample artifacts** (synthetic, realistic shape):
[`audit.md`](../tests/fixtures/real_session/audit.md) ·
[`metrics.json`](../tests/fixtures/real_session/metrics.json) ·
[`transcript.txt`](../tests/fixtures/real_session/transcript.txt) — everything a single session leaves on disk after `session_end`. Field reference — [`tests/fixtures/real_session/README.md`](../tests/fixtures/real_session/README.md).

## Backlog state machines

The framework's backlog lives in three parallel state machines: tasks (`HATS-NNN`), hypotheses (`HYP-NNN`), and proposals (`PROP-NNN`). All three are managed through the `ai-hats task` CLI and serialized as YAML under `.agent/`.

<p align="center">
  <img src="assets/diagrams/backlog-task-fsm.svg" alt="Task state machine" width="640">
  <br><sub><b>Task (HATS-NNN)</b></sub>
</p>

<p align="center">
  <img src="assets/diagrams/backlog-hyp-fsm.svg" alt="Hypothesis state machine" width="380">
  <br><sub><b>Hypothesis (HYP-NNN)</b></sub>
</p>

<p align="center">
  <img src="assets/diagrams/backlog-prop-fsm.svg" alt="Proposal state machine" width="380">
  <br><sub><b>Proposal (PROP-NNN)</b></sub>
</p>

<!-- Sources: docs/assets/diagrams/backlog-{task,hyp,prop}-fsm.d2 -->


- **Task (`HATS-NNN`)** — a unit of planned work. Happy path — the fixed pipeline `brainstorm → plan → execute → document → review → done` without skipping states. Side routes: `blocked` (returnable to `plan` or `execute`), `failed` (recoverable via `brainstorm`), `cancelled` (administrative close from any non-terminal state), and from `done` a reopen path to `execute` is available for finishing epic scope. On the transition to `plan` a `plan.md` scaffold is created; the work log is written with session tracking; a file lock protects against race conditions.
- **Hypothesis (`HYP-NNN`)** — a claim about system or process behavior. Stays `active` while sessions accumulate verdicts in `validation_log`; closes into `confirmed`, `refuted`, or `stalled` per `exit_criteria`. Verdicts are written by reflect-session (see below).
- **Proposal (`PROP-NNN`)** — an improvement suggestion: either from reflect-session on self-problem, or filed by hand. Stays `open` until triaged in `reflect all` → `accepted` / `rejected` / `deferred` / `duplicate`.

### Searching tasks

`--search` accepts a regex (case-insensitive) and matches against id, title, description, tags, parent_task, and depends_on:

```bash
ai-hats task list --search epic              # all epics (by tag or title)
ai-hats task list --search HATS-092          # epic + children (parent_task) + tasks blocked by it (depends_on)
ai-hats task list --search docs              # anything mentioning docs (id/title/desc/tags)
ai-hats task list --search "HATS-09[2-3]"    # regex: two epics at once
ai-hats task list --search worktree --all    # including done/failed
```

## Reflection loop

Every session becomes a structured retrospective: a pure-Python factual layer (metrics, files, commits, closed tasks) plus an LLM narrative with verdicts on active HYPs and votes on PROPs. Auto-retro is triggered by the `session_end` hook per the `off | always | smart | hint` policy.

The cycle has two parts: **auto reflect-session** (per session) feeds the HYP log and PROP inbox; **manual reflect-all** (user-initiated) triages the accumulated backlog.

**Sample artifacts** (synthetic, realistic shape):
[`session-review.md`](../tests/fixtures/real_session/session-review.md) — `hats-session-review/v1` for a single session (`hypothesis_verdicts[]`, `proposal_actions[]`, `self_problems[]`) ·
[`HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) — a hypothesis with an append-only `validation_log` ·
[`PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml) — a proposal with co-sign `votes[]`. Field reference — [`tests/fixtures/real_backlog/README.md`](../tests/fixtures/real_backlog/README.md).

### Auto reflect-session (per session)

Triggered after `session_end` when `policy ∈ {always, smart}` and the threshold is met. One LLM call in the session-reviewer role; the output is verdicts on every active HYP and an optional self-problem PROP. The persistent artifacts — HYP `validation_log` and PROP inbox — become inputs to manual triage.

<p align="center">
  <img src="assets/diagrams/auto-reflect-session.svg" alt="Auto reflect-session diagram" width="520">
</p>

<!-- Source: docs/assets/diagrams/auto-reflect-session.d2 -->


### Manual reflect-all (triage)

When HYPs and PROPs have piled up — the user runs `ai-hats reflect all`. Pre-flight builds a handoff from active HYPs and open PROPs, then an interactive chat, and finally `reflect commit` flips statuses in bulk.

<p align="center">
  <img src="assets/diagrams/manual-reflect-all.svg" alt="Manual reflect-all diagram" width="520">
</p>

<!-- Source: docs/assets/diagrams/manual-reflect-all.d2 -->


Full guide (policies, session-reviewer, manual triage, hypothesis workflow) — see **[how-to-feedback-loop.md](how-to-feedback-loop.md)**.

## Project structure

```
.agent/                                # Active components (generated)
  rules/                               # Physical copies of rules from the role
  skills/                              # Physical copies of skills
  hooks/                               # Hook scripts
  backlog/
    tasks/<ID>/                        # Task card + plan.md + retrospective.md
    proposals/PROP-NNN.yaml            # Improvement proposals (see task proposal)
  STATE.md                             # Tabular index + current task state
  hypotheses/HYP-NNN.yaml              # Hypothesis backlog (see task hyp)
  retrospectives/
    sessions/<id>.md                   # SessionReviewV1 (facts + narrative + HYP verdicts + PROP actions)
<ai_hats_dir>/sessions/runs/
  session_<ID>/                        # trace.log, audit.md, metrics.json, transcript.txt
ai-hats.yaml                           # Project config + role + feedback
GEMINI.md / CLAUDE.md                  # System prompt
```

## Library layout

The shipped library is split into two layers, both shipped inside the installed package (`ai_hats.library` sub-package, sourced from the repo-root `library/` directory):

```
library/
  core/                              # engine fundament — required at runtime
    roles/          initial-wizard, session-reviewer, auditor-for-role, judge-for-role, hypothesis-intake, test-agent
    traits/         trait-base, trait-agent, trait-analyst-base, base-judge, base-auditor, trait-reflect-mode
    rules/          global_rule_*, rule_backlog_discipline, dev_rule_edit_efficiency, dev_rule_tool_call_hygiene
    skills/         backlog-manager, backlog-create, context-*, review-*, judge-*, role-coherence-protocol, request-supervisor, ...
    pipelines/      execute, human, reflect-{session,role,all,issue}
    initial_injections/   initial-wizard, reflect-all, reflect-role
    templates/      claude/CLAUDE.md.template (provider scaffold)
  usage/                             # curated content catalog — opt-in
    roles/          assistant, architect, judge, sre, go-dev, go-dev-full
    traits/         trait-se-mindset, trait-researcher-mindset, skill-engineer, dev::python, dev::shell, dev::go-*, env::proxmox
    rules/          dev_rule_secure_coding, env_rule_proxmox_infra
    skills/         55+ skills (golang-*, terraform, ansible, observability, system-design, ...)
```

The `core/` vs `usage/` split is informational; both are loaded by `Assembler._build_library_paths`. User overrides layer on top via `~/.ai-hats/`, `ai-hats.yaml: library_paths`, and `<project>/libraries/` — see [extending.md](extending.md).

Vendored golang-* skills carry the upstream commit SHA, LICENSE, and attribution in `metadata.yaml.upstream.*` — the foundation for a future plugin system (see HATS-050).

### Skill template

Every skill follows the canonical format (see `skill-template`):

```markdown
# Skill Name
One-line purpose.

## When to Use         ← activation triggers
## <Main Section>      ← Procedure | Checklist | Workflow | Conventions
## Completion          ← completion criteria
## Anti-Patterns       ← common mistakes
```

Patterns: `protocol`, `checklist`, `orchestrator`, `reference`, `template`.
Metadata: `metadata.yaml` (name, description, author, tags, pattern).

A skill may optionally declare **git hooks**, which are installed
automatically into `.githooks/` when the role is built (HATS-088):

```yaml
# <skill>/metadata.yaml
git_hooks:
  pre-commit:
    - git_hooks/check.sh   # path relative to the skill directory
```

The builder copies scripts to `.githooks/<event>.d/<skill>-<basename>`,
generates a dispatcher at `.githooks/<event>`, and sets
`core.hooksPath = .githooks` idempotently. If the user has already
configured a `core.hooksPath` or has their own dispatcher without our
marker — those are not touched; a warning with instructions is printed.

### Sample role config.yaml

```yaml
name: assistant
priorities:
  - Reliability
  - Cleanliness
  - Velocity
composition:
  traits:
    - trait-base
    - trait-agent
    - dev::python
  rules:
    - dev_rule_git_workflow
  skills:
    - backlog-manager
    - git-mastery
injection: |
  # ROLE: PRIMARY AUTOMATION ASSISTANT
  ...
```
