---
name: retro-to-framework
description: Convert an observation about agent behavior — from a retrospective or from a single incident — into a framework-level change (hook, rule, skill, skill update). Use after a retrospective identifies problems that are not project-specific, after an incident where an agent misapplied a component it had read, when CLAUDE.md band-aids accumulate (more than 3 per-project rules that could be generic), or when the same problem recurs across multiple projects.
license: MIT
---

# Retro-to-Framework

Convert project-level retrospective findings into framework-level improvements.

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. When running `ai-hats self init` (step 5), define a resolver once (host launcher on PATH, else the project venv's interpreter — no `bin/ai-hats` console script):
>
> ```bash
> ah() { if command -v ai-hats >/dev/null 2>&1; then ai-hats "$@"; else ./.venv/bin/python -m ai_hats "$@"; fi; }
> ah self init
> ```
>
> If neither works, the project's venv interpreter lives at `./.venv/bin/python` (invoke the package as `./.venv/bin/python -m ai_hats …`). Resolve the path explicitly — falling back blindly wastes a turn.

## When to Use

Two entry points, one procedure:

- **Downstream of a retro** — **self-retrospective** produces the findings, this
  skill promotes the generic ones.
- **Downstream of an incident** — one session where an agent observably did the
  wrong thing. No retro needed; the evidence is the session itself. Bring what
  the agent did, what the component told it to do, and the proof it read that
  component (otherwise the finding is "the agent never saw it", a different fix).

Two boundaries — the finding must be **cross-project generic** (a project-local
fix stays in that project's CLAUDE.md), and trimming or dedup of components that
already exist is **skill-optimization**, not this.

## Procedure

1. **Classify findings:**
   For each retrospective finding, ask: "Would this same problem occur in a
   different project using the same role?"
   - YES → framework candidate (rule, skill, or skill update)
   - NO → project-specific (stays in project CLAUDE.md)

2. **Map to component type — mechanism first:**
   | Finding type                                        | Framework component               |
   | --------------------------------------------------- | --------------------------------- |
   | Invariant a machine can decide ("never write X to Y") | Hook / gate / CLI check — not prose |
   | Behavioral constraint ("always do X")               | Rule                              |
   | Multi-step process ("when X, do Y then Z")          | Skill                             |
   | Missing check in existing process                   | Skill update                      |
   | Knowledge gap                                       | Reference doc or injection update |

   The first row is first on purpose: prose costs tokens every turn and asks the
   agent to comply, a hook costs nothing and does not ask. Rank the options with
   the mechanism ladder in trait `skill-engineer` before picking a row.

   **If prose for this finding was already tried and did not hold, the first row
   is the only honest answer.** A component the agent demonstrably read and did
   not apply will not be fixed by rewording it in the same place — that is the
   evidence that the mechanism, not the wording, is wrong. Record what was tried
   and how it was measured, so the next reader does not re-run the experiment.

3. **Draft the improvement:**
   Follow **skill-template** for new skills, rule naming convention for rules.
   Include retrospective ID as provenance (e.g., "Source: GERX-002").

4. **Wire into composition:**
   Determine which trait should include the new component.
   Update trait config.yaml. Run composer validation for all affected roles.

5. **Propagate to projects:**
   Roles are composed fresh at every session launch, so a library edit needs no
   per-project command — it is live for the next session. A project pinned to a
   published `ai-hats-library` needs `ai-hats self update` to pull the new
   version; `self init` does not fetch one.
   Remove corresponding band-aids from the project's own CLAUDE.md.
   Verify with `ai-hats config status` (composition tree) or `ai-hats --dry-run`
   (the exact artifacts a launch would deliver).

6. **Close the loop:**
   Update the original retrospective with a link to the framework change.
   Create a HATS task if the change is non-trivial.

## Completion

- Findings classified as framework vs project-specific
- Framework improvements implemented and validated (composer 0 errors, tests green)
- All projects using affected roles bumped
- Per-project band-aids removed
- Original retrospective updated with provenance

## Anti-Patterns

- Leaving band-aids in project CLAUDE.md after framework fixes exist — remove them
- Making everything a framework change — some things are truly project-specific
- Skipping composer validation — always verify after wiring changes
