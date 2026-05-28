---
name: skill-template
description: Canonical template and validation guide for ai-hats skills. Use when creating a new skill, reviewing an existing one for structural compliance, or deciding which pattern fits a new behavior.
---
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

## Scripts vs prose

Add a `scripts/` utility (rather than prose the agent re-generates) when
the operation is **deterministic, repeatable, and benefits from explicit
error handling**. A committed script saves tokens and is more reliable
than code regenerated from a description each time. Keep ad-hoc, one-off,
or judgment-heavy steps as prose.

## Validation Checklist

- [ ] H1 + one-liner present
- [ ] **Description includes BOTH capability AND trigger conditions** — it
  is the *only* thing the skill selector sees. "What it does. Use when
  <specific triggers>."
- [ ] `## When to Use` section with concrete trigger conditions
- [ ] Main body uses the correct heading for its pattern
- [ ] `## Completion` present (unless reference/template pattern)
- [ ] `## Anti-Patterns` present (unless trivially small)
- [ ] `metadata.yaml` exists alongside SKILL.md
- [ ] **References one level deep** — `SKILL.md → references/*.md`, never
  `references/*.md → references/sub/*.md`. No reference pyramids.
- [ ] **Length policy:** `≤50` lines ideal · `50–150` warning (justify in
  the skill or split) · `>150` must split — move content into
  `references/` or sibling skills. If a skill cleanly divides into two
  domains, split it.

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
