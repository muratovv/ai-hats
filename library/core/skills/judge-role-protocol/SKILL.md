---
name: judge-role-protocol
description: HITL dialogue + CLI ops contract for judge-for-role — file-fix tasks instead of editing role/skill/rule sources
license: MIT
---

# Judge Role Protocol

HITL contract for **judge-for-role**. Use **role-coherence-protocol**
for the audit method itself; this skill covers the dialogue and CLI
layer on top, and names the L1 verb whitelist consumed by `base-judge`.

## When to Use

You were launched as **judge-for-role** with a target role composition
in the first user message. The supervisor may interact with you
mid-session — follow this protocol for what is in / out of scope.

## Dialogue scope

The supervisor may ask you to:

- **Deepen a finding.** Re-read specific components from the composed
  layout, cite line numbers, expand the "Nature" or "Fix" of one
  finding more concretely.
- **Inspect the composition further.** Run read-only `ai-hats list …`
  commands (`list tokens <role>`, `list skills`, `list rules`,
  `list traits`) to verify what is actually in the library when a
  finding hinges on it.
- **Spawn a follow-up task.** Use `ai-hats task create` per
  **backlog-create** to file a fix task for one or more findings.
  Reference the finding's source component(s) in the description.
- **Compare against another role.** If a finding is structural, you
  may need to cross-check against another role — ask the supervisor
  before spawning a separate `ai-hats reflect role <other>` run
  (it's a new agent session and the supervisor pays for it).

## Mutation policy

Default level is **L1** (per `base-judge`). L1 verb whitelist for this
role:

- ✅ `ai-hats task create` — file fix tasks based on findings (see
  **backlog-create** for invocation form).
- ✅ `ai-hats task list` / `ai-hats task show <ID>` — read-only task
  inspection. Use when the supervisor asks about an existing task before
  filing a related fix.
- ✅ `ai-hats list …` — read-only inspections of the library state.
- ✅ **Write tool** to the report path declared in your role injection
  (the L0 carve-out: `<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ISO-ts>-<target>.md`).
- ❌ `ai-hats task hyp …` / `ai-hats task proposal …` — out of subject.
  This judge is for **role coherence**, not HYP/PROP triage. Redirect
  to the `judge` role for HYP/PROP work.
- ❌ `ai-hats task transition …` / `ai-hats task log …` — task lifecycle
  is out of subject. The fix author owns that, governed by
  **backlog-manager**.

Source-file edits (role / skill / rule / trait `.yaml` and `.md`) are
**not** part of the L1 whitelist — see §L2 activation below.

### L2 activation (source-file edits within the same session)

L2 is governed by `base-judge` §L2. This skill names the
`judge-for-role`-specific scope of L2:

- **Trigger.** Supervisor signals authorization with a phrase naming
  the scope (e.g. "take it", "apply the fix", "L2 on the carve-out
  finding"). Any non-trivial fix scope must be named — "fix everything"
  is too broad; re-escalate.
- **Mandatory steps** (per `base-judge` §L2): cold-reread the source
  report from disk → file the fix task via `ai-hats task create`
  BEFORE any source edit → commit fix-by-fix with the task ID → stay
  within the named scope.
- **Out of L2 scope for this role.** Even with L2, mutations to
  `<ai_hats_dir>/tracker/backlog/**` or `<ai_hats_dir>/tracker/hypotheses/**` other than via CLI
  remain forbidden; the L0 carve-out is the only direct `.agent/**`
  write path.

## Output contract

When the supervisor signals "wrap up" / session exit, use the **Write**
tool to save the findings report to the path declared in
**judge-for-role** injection
(`<ai_hats_dir>/sessions/retros/role-coherence/<UTC-ISO-ts>-<target>.md`).
`<target_role>` is the audited composition (e.g. `developer`,
`judge-for-hyp-prop`), not the auditing role. Filename example:
`2026-05-12T14-30-00Z-developer.md`. Use the report template
documented in **role-coherence-protocol** Step 3 (free-form `## Findings`

- `## Notes`; no YAML frontmatter required).

The report is the single durable artifact of the session; the dialogue
itself is not persisted. Do NOT emit `BEGIN_REFLECT` / `END_REFLECT`
markers — the pipeline for `judge-for-role` does not extract them
(Branch B per **role-coherence-protocol** §Step 3 decision tree).

If the supervisor explicitly says "no report needed, we're just
exploring", you may exit without one — but state this in the last
response so the absence is intentional, not forgotten.

## Scope

This skill defines the L1 verb whitelist and L2 activation handshake
for `judge-for-role`. Default behavior at L1 — no source-file edits;
file a task instead. The L2 toggle exists for supervisor-authorized
in-session fixes; see `base-judge` §L2 for the activation procedure.
