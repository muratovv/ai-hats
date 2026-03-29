# Skill Template

Guide for creating and validating skills in the ai-hats library.

## When to Use
- Creating a new skill
- Reviewing an existing skill for structural compliance
- Deciding which pattern fits a new behavior

## Canonical Structure

Every SKILL.md follows this layout:

```
# <Skill Name>

<One-line purpose.>

## When to Use
<Trigger conditions — when the agent should activate this skill.>

## <Main Section>
<Core content. Section name depends on pattern (see below).>

## Completion                ← required for protocol/checklist; optional for reference
<Done-criteria and expected artifacts.>

## Anti-Patterns             ← recommended
<Common mistakes, 2-5 bullets.>
```

## Patterns

Choose the main section heading by pattern:

| Pattern | Main Heading | Used When |
|---------|-------------|-----------|
| protocol | Procedure | Step-by-step process with ordered phases |
| checklist | Checklist | Verification / audit with pass/fail items |
| orchestrator | Workflow | State machine coordinating other skills |
| reference | Conventions | Declarative guidelines, no procedure |
| template | Format | Prescribes a specific output structure |

## Validation Checklist

- [ ] H1 + one-liner present
- [ ] `## When to Use` section with concrete trigger conditions
- [ ] Main body uses the correct heading for its pattern
- [ ] `## Completion` present (unless reference/template pattern)
- [ ] `## Anti-Patterns` present (unless trivially small)
- [ ] `metadata.yaml` exists alongside SKILL.md
- [ ] Total length ≤ 50 lines (orchestrators may exceed)

## References

For detailed guidance on success metrics, testing, and troubleshooting,
see `references/anthropic-skills-guide.md`.

For external frameworks (skillcreator, Anthropic skill-creator),
see `references/external-skill-frameworks.md`.

## Anti-Patterns
- Dumping hundreds of lines into one SKILL.md — split heavy content into `references/`
- Vague "When to Use" that triggers on everything — be specific
- Mixing multiple domains in one skill — split into focused skills
- Omitting completion criteria — the agent won't know when to stop
