# Synthetic backlog artifacts

Realistic-shape fixtures of HYP and PROP YAML files. **Synthetic** — IDs
and content are fabricated; the structure mirrors production
`<ai_hats_dir>/tracker/{hypotheses,backlog/proposals}/` entries.

| File | What it contains |
|---|---|
| [`HYP-001-sample.yaml`](HYP-001-sample.yaml) | Hypothesis backlog entry — claim + `success_criterion` + `observation_window` + `exit_criteria`, append-only `validation_log` populated by reflect-session over time. |
| [`PROP-001-sample.yaml`](PROP-001-sample.yaml) | Proposal backlog entry — `category`, `target`, `rationale`, co-sign `votes[]` from sessions that touched the same area. |

Both are manipulated exclusively via `ai-hats task hyp ...` and
`ai-hats task proposal ...` CLI subcommands — never by hand-editing.

See [`docs/ARCHITECTURE.md`](../../../docs/ARCHITECTURE.md#reflection-loop)
for where they sit in the auto + manual reflect cycles, and the
[Backlog state machines](../../../docs/ARCHITECTURE.md#backlog-state-machines)
diagram for the transitions between statuses.
