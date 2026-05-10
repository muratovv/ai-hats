---
name: role-coherence-protocol
description: Audit a composed role against the user's project context for contradictions and interference; produce free-form findings with proposed fixes
---

# Role Coherence Protocol

Audit protocol for the **role_reviewer** role. Verifies that a target
role's composed instructions are internally consistent and do not
interfere with the user's project files (`./CLAUDE.md`,
`.agent/ai-hats/user-rules/*.md`). Run by `ai-hats reflect role <name>`
and `ai-hats reflect roles`.

## When to Use

You were launched as **role_reviewer**. The first user message contains
the target role's composed text plus the project context that should be
audited against it. Apply this protocol end-to-end and write the report
between the markers before exiting.

## Inputs

The session opens with three named blocks in the first user message:

1. **Target role** — the role being audited, fully composed (traits +
   role-injection + skills/rules text). This is what the user's session
   would see if they ran with that role active.
2. **Project CLAUDE.md** — the project's user-owned root prompt
   (`./CLAUDE.md`). May be empty.
3. **User rules overlay** — concatenated content of
   `.agent/ai-hats/user-rules/*.md`. May be empty.

If a block is empty, note it and continue (a missing user-rules layer
is normal for fresh projects).

## Procedure

### Step 1 — Re-read inputs carefully

The target role and user context are already in your message. Do not
re-fetch them from disk. Skim once for shape, then re-read each block
section by section, extracting concrete instructions / forbidden
patterns / required behaviours.

### Step 2 — Find conflicts

Walk through the target role looking for:

- **Forbidden-token conflicts.** A trait or skill recommends a tool
  that an always-on rule forbids (canonical example:
  `## SHELL DEVELOPMENT` recommends `rg`/`fd` while
  `dev_rule_tool_call_hygiene` forbids them).
- **Internal contradictions.** Two instructions inside the role
  contradict each other (e.g. one trait says "be terse", another says
  "always include rationale").
- **User-context interference.** A role instruction conflicts with
  something the user wrote in their CLAUDE.md or user-rules overlay.

For each conflict capture:

- **Location** — section heading or short quote of each side.
- **Nature** — what contradicts what, in one sentence.
- **Recommendation** — concrete fix proposal (rephrase, drop,
  override, split into role variant, etc.).

### Step 3 — Write the report

Before the session ends, emit the report between markers so the
pipeline `extract_marker` step can capture it:

```
BEGIN_REFLECT

# Role coherence report — <target_role> · <UTC ts>

## Findings
1. **<short label>** — <Location>. <Nature>. **Fix:** <recommendation>.
2. ...

## Notes
<free-form observations, structural smell, anything outside finding scope>

END_REFLECT
```

Empty findings are valid — write `(none)` under `## Findings` and
explain why in `## Notes` (e.g. "role contains only `trait-base`,
nothing to conflict with").

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

You DO NOT mutate any files during the audit. The pipeline's
`save_artifact` step persists your report — your only job is producing
the markdown between the markers. Do not run `ai-hats` CLI commands;
do not edit role/rule/skill source files even if you spot a typo.
