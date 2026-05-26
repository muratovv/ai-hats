# Rule: Backlog Discipline

Applies to all three backlog item types — **tasks** (`HATS-NNN`), **hypotheses** (`HYP-NNN`), and **proposals** (`PROP-NNN`).

1. **CLI-only.** All backlog operations via `ai-hats task` CLI (`task ...`, `task hyp ...`, `task proposal ...`, `task attach ...`). Never read or edit `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` directly. This covers the whole `tasks/<ID>/**` subtree — task.yaml AND the `attachments/` folder. Direct `mkdir`/`mv`/`echo > task.yaml`/`sed -i` under the backlog are violations; the `pre-commit-attachments` hook (HATS-402) catches the attachments side. **Carve-out for `plan.md`:** `tasks/<ID>/plan.md` is editable directly via Write/Edit — it is the role's primary plan-stage deliverable, not a state mutation (parallel to the `base-judge §L0 carve-out` for analyst reports). The scaffold is created by `task transition <ID> plan`; the content is the agent's responsibility. See **backlog-manager** for the canonical flow.
2. **Work log cadence.** Log after every significant action on a task: approach changes, file deletions, branch operations, milestone completions. For HYP/PROP, append to `validation_log` / `votes` via CLI.
3. **State transitions immediate.** Transition state when work changes phase — no stale states. Applies to task lifecycle (`brainstorm → … → done`), HYP status (`active → confirmed | refuted | stalled`), and PROP status (`open → accepted | rejected | deferred | duplicate`).
4. **Completion gate.** A task is `done` only when: state is `done`, work_log has a final entry, STATE.md is synced.
5. **HYP vs technical risk.** HYP / PROP backlog is reserved for **agent-behaviour hypotheses** (how the agent makes decisions, which triggers fire, which practices yield results). **Do NOT file** HYP / PROP for carry-over technical / migration / harness / documentation / coordination risks surfaced in review — those go to `ai-hats task log <FOLLOW_UP_ID>` (or as plan-stage additions on the relevant task), not to a new HYP "just in case". Cost rationale: HYP / PROP carry expensive scaffolding (validation_log, votes, judge triage in reflect-all). Concrete technical risks bound to a known task live there. If a risk isn't bound to a concrete task and isn't behavioural either — memory or skip; "we'll see it when we get there" is acceptable.

## Scope

§1 applies to every role that touches the backlog (filing, lifecycle, or read).

§§2–4 apply only to roles that **own a backlog item's lifecycle** — the fix author / lead / primary agent. Roles whose protocol skill whitelists only `task create` (e.g. L1 analyst roles like `judge-for-role`) follow §1 only; they never enter §§2–4 obligations because they never own a lifecycle.

For CLI commands, lifecycle details, and plan-flow procedures → skill **backlog-manager**.
