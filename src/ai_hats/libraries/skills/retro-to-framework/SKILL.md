---
name: retro-to-framework
description: Convert project retrospective findings into framework-level improvements (rules, skills, skill updates)
---
# Retro-to-Framework

Convert project-level retrospective findings into framework-level improvements.

> **Invocation in a harness shell.** Harness-spawned bash does not inherit an activated venv. When running `ai-hats bump` (step 5), resolve the binary once:
> ```bash
> AH="$(command -v ai-hats || echo ./.venv/bin/ai-hats)"
> "$AH" bump
> ```
> If neither works, the project's venv lives at `./.venv/bin/ai-hats`. Resolve the binary path explicitly — falling back blindly between `ai-hats` and the venv path wastes a turn.

## When to Use
- After a retrospective identifies problems that are NOT project-specific
- When CLAUDE.md band-aids accumulate (>3 per-project rules that could be generic)
- When the same problem recurs across multiple projects

## Procedure

1. **Classify findings:**
   For each retrospective finding, ask: "Would this same problem occur in a
   different project using the same role?"
   - YES → framework candidate (rule, skill, or skill update)
   - NO → project-specific (stays in project CLAUDE.md)

2. **Map to component type:**
   | Finding type | Framework component |
   |---|---|
   | Behavioral constraint ("always do X") | Rule |
   | Multi-step process ("when X, do Y then Z") | Skill |
   | Missing check in existing process | Skill update |
   | Knowledge gap | Reference doc or injection update |

3. **Draft the improvement:**
   Follow **skill-template** for new skills, rule naming convention for rules.
   Include retrospective ID as provenance (e.g., "Source: GERX-002").

4. **Wire into composition:**
   Determine which trait should include the new component.
   Update trait config.yaml. Run composer validation for all affected roles.

5. **Propagate to projects:**
   In each project using the affected role: `ai-hats bump`.
   Remove corresponding band-aids from project CLAUDE.md.
   Verify the framework version appears in the generated prompt.

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
