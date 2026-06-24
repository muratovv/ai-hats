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
<Boundaries & disambiguation the one-line description can't carry:
when NOT to use, which sibling skill to prefer, scope edges.
Complements the description's triggers — does not restate them.>

## <Main Section>
<Core content. Section name depends on pattern (see below).>

## Completion                ← required for protocol/checklist; optional for reference
<Done-criteria and expected artifacts; include the validation scenario.>

## Anti-Patterns             ← recommended
<Common mistakes, 2-5 bullets.>
```

### `description` vs `## When to Use` — two lifecycle stages

They are read at different moments, so they carry different content:

- **`description`** is in the always-on skill index (selection-time). It
  is the *only* thing the selector sees, so it holds the **triggers** —
  "what it does. Use when <X>". It must NOT summarize the procedure body:
  a step-by-step summary in the index is a shortcut the selector acts on
  *instead of* loading the skill, so the actual procedure never runs.
- **`## When to Use`** loads only *after* the skill is invoked
  (post-load). The "should I use this" call is already made by then, so
  restating triggers adds nothing. Use it for **boundaries &
  disambiguation**: when NOT to use, which sibling skill to prefer,
  scope edges — the nuance a one-line description can't hold.

## Patterns

Choose the main section heading by pattern:

| Pattern      | Main Heading | Used When                                 |
| ------------ | ------------ | ----------------------------------------- |
| protocol     | Procedure    | Step-by-step process with ordered phases  |
| checklist    | Checklist    | Verification / audit with pass/fail items |
| orchestrator | Workflow     | State machine coordinating other skills   |
| reference    | Conventions  | Declarative guidelines, no procedure      |
| template     | Format       | Prescribes a specific output structure    |

## Scripts vs prose

Add a `scripts/` utility (rather than prose the agent re-generates) when
the operation is **deterministic, repeatable, and benefits from explicit
error handling**. A committed script saves tokens and is more reliable
than code regenerated from a description each time. Keep ad-hoc, one-off,
or judgment-heavy steps as prose.

A skill's `SKILL.md` frontmatter can also declare **hooks** under a top-level
`ai_hats:` key — `git_hooks` (git events) and `runtime_hooks` (Claude Code
`PreToolUse` / `PostToolUse`). See `docs/how-to-extend.md`.

## Validation scenario (RED → GREEN → REFACTOR)

A skill is not done until one **named baseline scenario** shows it changes
behaviour — the same discipline we already apply to code (HATS-645):

- **RED** — name a concrete task where an agent *without* this skill gets it
  wrong (the failure the skill exists to prevent). If you can't name one, the
  skill has no demonstrable value — stop and reconsider.
- **GREEN** — the skill's guidance makes that same task come out right.
- **REFACTOR** — tighten wording to close rationalization loopholes: an agent
  should not be able to read the skill and still talk itself out of compliance.

Prose-level, not an eval harness: one scenario named in the skill body or its
task card — no `evals.json`, no scoring. (Adapted from obra/superpowers
writing-skills, MIT.)

## Validation Checklist

- [ ] H1 + one-liner present
- [ ] **Description includes BOTH capability AND trigger conditions** — it
      is the *only* thing the skill selector sees. "What it does. Use when
      <specific triggers>."
- [ ] **Description does NOT summarize the procedure body** — one capability
      phrase is fine; enumerating the skill's steps/method is not. A body-summary
      in the always-on index is a shortcut the selector acts on *instead of*
      loading the skill, so the procedure gets skipped. (CSO discipline adapted
      from obra/superpowers, MIT.)
- [ ] **Validation scenario present** — one named RED baseline (an agent
      *without* the skill fails) that the skill demonstrably fixes; see
      "Validation scenario" above
- [ ] `## When to Use` adds boundary/disambiguation value beyond the
      description (when NOT to use, sibling-skill preference, scope edges) —
      not a restatement of the description's triggers
- [ ] Main body uses the correct heading for its pattern
- [ ] `## Completion` present (unless reference/template pattern)
- [ ] `## Anti-Patterns` present (unless trivially small)
- [ ] Frontmatter declares `name` + `description` (single-file SKILL.md — no
      `metadata.yaml` sidecar; any hooks go under a top-level `ai_hats:` key)
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
- `## When to Use` that merely restates the description — once the skill
  is loaded the selection decision is already made; give boundaries /
  disambiguation / when-NOT instead
- Vague triggers in the `description` that match everything — be specific
- **`description` that summarizes the procedure body** — enumerating the
  skill's steps in the always-on index lets the agent act on the summary and
  skip loading the skill; keep it triggers + one capability phrase
- Mixing multiple domains in one skill — split into focused skills
- Omitting completion criteria — the agent won't know when to stop
- **Shipping a skill with no validation scenario** — if no RED baseline shows
  an agent failing without it, the skill's value is unproven
