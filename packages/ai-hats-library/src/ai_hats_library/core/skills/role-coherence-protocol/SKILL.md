---
name: role-coherence-protocol
description: "Audit a composed role against the user's project context for contradictions and interference, producing free-form findings with proposed fixes. Use when launched as a role-auditing role (auditor-for-role, judge-for-role, or sibling) whose first user message contains the target role's composed text plus the project context to audit against it."
license: MIT
---

# Role Coherence Protocol

Audit method for any role-auditing role (`auditor-for-role`,
`judge-for-role`, and future role-audit family). Verifies that a target
role's composed instructions are internally consistent and do not
interfere with the user's project files (`./CLAUDE.md`,
`.agent/ai-hats/user-rules/*.md`). Run by `ai-hats reflect role <name>`
and `ai-hats reflect roles`.

The skill defines *what to audit and how to structure findings*.
Mutation policy and dialogue contract are set by the composing role's
base trait — `base-auditor` (no CLI / no dialogue) vs `base-judge`
(CLI ops + HITL dialogue, governed by **judge-role-protocol**).

## When to Use

You were launched as a role-auditing role (`auditor-for-role`,
`judge-for-role`, or sibling). The first user message contains the
target role's composed text plus the project context that should be
audited against it. Apply this protocol end-to-end and deliver the
report per §Step 3 — the delivery branch (markers vs Write tool) is
chosen by your composing base trait.

## Inputs

The first user message points at three groups of files on disk. Read
them via the **Read** / **Glob** tools — do not ask the user to paste
content; the harness has already materialized everything.

### 1. Target role composition (layered)

Path: provided to you in the supervisor's first message via the
`{composed_dir}` interpolation — use that path verbatim. Do not
reconstruct it. Typical shape (per-session, HATS-308):
`<project>/<ai_hats_dir>/sessions/runs/pipeline_runs/reflect-role/<session_id>/composed/<target_role>/`.
Layout:

- `manifest.yaml` — start here. Contains `name`, `priorities`, and
  the names of bundled `traits` / `rules` / `skills`.
- `role-injection.md` — the role's own injection text (if non-empty).
- `overlay-injection.md` — project-overlay's appended text (if any).
- `traits/<name>.md` — per-trait injection text (deduped: a trait
  whose text already appeared elsewhere is omitted but still listed
  in the manifest).
- `rules/<name>.md` — full body of each bundled `rule.md`.
- `skills/<name>.md` — full body of each bundled `SKILL.md`.

This breakdown is *richer* than what the user's session sees at
runtime (which flattens everything into a single system prompt). It
lets you trace every instruction back to its source component when
reporting findings.

### 2. Project CLAUDE.md

Path: `<project_dir>/CLAUDE.md` — user-owned root prompt. May not
exist on fresh projects.

### 3. User rules overlay

Path: `<project_dir>/.agent/ai-hats/user-rules/*.md` — project-specific
overrides. Use `Glob` to enumerate. Directory may be empty or absent.

If a group is empty / missing, note it and continue (a missing
user-rules layer is normal for fresh projects).

## Procedure

### Step 1 — Read the manifest, then walk components

Read `<composed_dir>/manifest.yaml` first to get the structure
(priorities + component names). Then:

- Read `role-injection.md` (and `overlay-injection.md` if present) for
  the role's intent.
- For every name in `composition.traits`, read `traits/<name>.md`.
- For every name in `composition.rules`, read `rules/<name>.md`.
- For every name in `composition.skills`, read `skills/<name>.md`.
- Read `<project_dir>/CLAUDE.md` if it exists.
- `Glob` `<project_dir>/.agent/ai-hats/user-rules/*.md` and read each.

Extract concrete instructions / forbidden patterns / required
behaviours from each component. Track them by source name — you'll
cite the source in every finding.

### Step 2 — Find conflicts

Walk the audit view component-by-component (use the manifest as the
checklist). For each pair of components, ask: do their instructions
agree? Categories of conflict to flag:

- **Forbidden-token conflicts.** A trait or skill recommends a tool
  that a bundled rule forbids (canonical example: a `## SHELL` trait
  recommends `rg`/`fd` while `dev_rule_tool_call_hygiene` forbids
  them).
- **Internal contradictions.** Two instructions inside the role
  contradict each other (e.g. one trait says "be terse", another says
  "always include rationale").
- **User-context interference.** A role instruction conflicts with
  something the user wrote in their CLAUDE.md or user-rules overlay.
- **Off-purpose components.** A bundled trait/rule/skill is unrelated
  to the role's stated purpose (priorities + role injection). Flag as
  a finding so the role author can drop it or justify it.

For each conflict capture:

- **Source** — name the component(s) involved (e.g. `trait: dev::shell`,
  `rule: dev_rule_tool_call_hygiene`, `skill: judge-protocol`,
  `role injection`, `user-rules:<filename>`).
- **Location** — section heading or short quote of each side.
- **Nature** — what contradicts what, in one sentence.
- **Recommendation** — concrete fix proposal (rephrase, drop,
  override, split into role variant, etc.).

### Step 3 — Write the report

**Delivery decision (read first, then template):**

```
Composed with `base-auditor`? → emit between BEGIN_REFLECT / END_REFLECT.
Composed with `base-judge`?    → Write tool to declared report path. No markers.
```

Pick the branch matching your composition and follow only that branch
below. The wrong branch ships markers the pipeline does not extract
(or vice versa).

Report template (used by both branches):

```
# Role coherence report — <target_role> · <UTC ts>

## Findings
1. **<short label>** — <Source(s)>. <Location>. <Nature>.
   **Fix:** <recommendation>.
2. ...

## Notes
<free-form observations, structural smell, anything outside finding scope>
```

Empty findings are valid — write `(none)` under `## Findings` and
explain why in `## Notes` (e.g. "role contains only `trait-analyst-base`,
nothing to conflict with").

**Branch A — `base-auditor` (batch, no pipeline interaction):**
emit the report between `BEGIN_REFLECT` / `END_REFLECT` markers in your
output. The pipeline's `extract_marker` + `save_artifact` steps capture
and persist it. Used by future batch QA gates; **no current pipeline
relies on this path**.

**Branch B — `base-judge` (HITL pipeline / manual interactive):** use
the **Write** tool to save the report directly to the path declared in
your role injection (typically
`<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ISO-ts>-<target>.md`). Do
NOT emit `BEGIN_REFLECT` / `END_REFLECT` markers — the pipeline does
not extract them on this path. Used by `judge-for-role` via
`ai-hats reflect role` and manual `ai-hats execute --role
judge-for-role`.

## Edge Cases

- **Empty user context.** If both `./CLAUDE.md` and `user-rules/` are
  empty, audit the role for self-consistency only and note in `## Notes`
  that no project context was available.
- **Same recommendation surfaces multiple places.** Group by Fix in
  `## Findings`; do not list duplicates.
- **Missing target role.** If the target-role block is empty, fail
  loudly: write a single `## Findings` entry pointing to the empty
  composition and exit.

## Scope

Mutation policy is defined by your composing base trait, not by this
skill. See the level declared there for what is allowed:

- `base-auditor` → **L0** (audit only): no CLI, no dialogue, single
  report artifact. Source-file edits unavailable.
- `base-judge` → **L1** (analysis + ack'd mutations): CLI verbs per
  `judge-role-protocol` whitelist, HITL dialogue, file tasks via
  `rack create`. Source-file edits gated by L2 activation
  (supervisor-authorized; see `base-judge` §L2).

This skill defines the audit method and report shape only. For default
L0 / L1 behaviour, defer to the base trait. For L2 source-file edits,
defer to `base-judge` §L2.

The pipeline's `save_artifact` step persists your report (Branch A) or
your direct Write lands it (Branch B) — emitting clean markdown is
your primary deliverable.
