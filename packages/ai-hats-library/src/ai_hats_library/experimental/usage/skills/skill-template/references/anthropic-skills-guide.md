# Anthropic Skills Guide — Reference Notes

Source: "The Complete Guide to Building Skills for Claude" (Anthropic, 2026)

## Progressive Disclosure (3 Levels)

1. **Frontmatter (YAML)** — Always in system prompt. Description field decides activation.
2. **SKILL.md body** — Loaded when Claude thinks skill is relevant.
3. **references/, scripts/, assets/** — Discovered on demand by Claude.

This minimizes token usage while maintaining specialized expertise.

## Description Field (Critical)

The description is how Claude decides whether to load the skill. Get this right.

**Structure:** `[What it does] + [When to use it] + [Key capabilities]`

**Good:** Specific, includes trigger phrases users would actually say.
**Bad:** Too vague ("Helps with projects"), too technical, missing triggers.

## Success Metrics

### Quantitative
- Skill triggers on 90% of relevant queries
- Completes workflow in fewer tool calls than without skill
- 0 failed API calls per workflow

### Qualitative
- Users don't need to prompt Claude about next steps
- Workflows complete without user correction
- Consistent results across sessions

## Testing Methodology

### 1. Triggering Tests
- Triggers on obvious tasks
- Triggers on paraphrased requests
- Does NOT trigger on unrelated topics

### 2. Functional Tests
- Valid outputs generated
- API calls succeed
- Edge cases covered

### 3. Performance Comparison
Run same task with and without skill. Measure:
- Tool calls count
- Total tokens consumed
- User corrections needed

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Skill not loading | Bad description | Add trigger phrases, be specific |
| Loads too often | Description too broad | Add negative triggers, clarify scope |
| Instructions ignored | Too verbose | Bullet points, frontload critical items |
| Instructions buried | Wrong priority | Put critical instructions at top |
| Ambiguous behavior | Vague language | "Run X" not "consider X" |
| Model skips steps | No encouragement | Add `## Performance Notes` section |
| Slow/degraded | Context bloat | Move details to references/, reduce enabled skills |

## Model Laziness Mitigation

Add explicit encouragement:
```
## Performance Notes
- Take your time to do this thoroughly
- Quality is more important than speed
- Do not skip validation steps
```

Note: Adding this to user prompts is more effective than in SKILL.md.

## Patterns

| Pattern | Use When | Key Technique |
|---------|----------|---------------|
| Sequential workflow | Multi-step processes in order | Explicit step ordering, validation at each stage |
| Multi-service coordination | Workflows span multiple tools | Clear phase separation, data passing between phases |
| Iterative refinement | Output improves with iteration | Quality criteria, refinement loop, know when to stop |
| Context-aware tool selection | Same outcome, different tools | Decision tree, fallback options, explain choices |
| Domain intelligence | Specialized knowledge needed | Domain expertise embedded in logic, compliance before action |

## Skill File Structure

```
your-skill-name/
  SKILL.md              # Required — main skill file
  scripts/              # Optional — executable code
  references/           # Optional — documentation loaded as needed
  assets/               # Optional — templates, fonts, icons
```

## Key Rules
- SKILL.md must be exactly `SKILL.md` (case-sensitive)
- Folder name: kebab-case, no spaces, no capitals, no underscores
- No XML tags (`<` or `>`) in frontmatter
- No README.md inside skill folder
- Keep SKILL.md under 5,000 words; use references/ for heavy content
