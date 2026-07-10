# External Skill Frameworks — Reference Notes

## elertan/skillcreator v3.1

Source: https://github.com/elertan/skillcreator

Meta-skill for autonomously generating high-quality Claude Code skills.

### 4-Phase Methodology
1. **Deep Analysis** — Understand use case, constraints, existing patterns
2. **Specification** — Define triggers, outputs, success criteria
3. **Generation** — Produce SKILL.md with frontmatter
4. **Multi-Agent Synthesis** — Peer review requiring unanimous 3/3 approval from Opus agents

### Quality Gates
- **Evolution Scoring:** >=7/10 on timelessness rubric
- **Peer Review:** 3 independent Opus agents must unanimously approve
- Skills must be "timeless" — avoid coupling to specific tool versions

### Key Insight
Use multiple agents as reviewers with different perspectives to catch blind spots.

---

## Anthropic Official skill-creator

Source: https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md

Built into Claude.ai and Claude Code.

### Core Development Loop
1. **Intent Capture** — Understand what the user wants to automate
2. **Research & Interview** — Ask clarifying questions about workflows
3. **Draft SKILL.md** — Generate with proper frontmatter and structure
4. **Test Cases** — Create 2-3 realistic prompts in `evals/evals.json`
5. **Run Evaluations** — Compare with-skill vs baseline
6. **Review Results** — Use eval viewer for analysis
7. **Iterate** — Refine based on feedback

### Key Techniques
- Generate skills from natural language descriptions
- Suggest trigger phrases and structure
- Flag common issues (vague descriptions, missing triggers, structural problems)
- Iterative improvement: bring edge cases back to skill-creator

### Usage
```
"Use the skill-creator skill to help me build a skill for [your use case]"
```

Note: skill-creator helps design and refine but does not execute automated test suites.

---

## Applicability to ai-hats

Both frameworks validate our approach:
- **Progressive disclosure** aligns with our frontmatter → SKILL.md → references/ pattern
- **Structured testing** (triggering + functional + comparison) maps to our Level 1-3 verification
- **Multi-agent review** concept maps to our audit-reviewer skill (3 perspectives)
- **Iterative refinement** maps to our retro-to-framework feedback loop

### What we can adopt
1. **evals.json format** — Structured test cases for skills (future: HATS-013 A/B testing)
2. **Evolution scoring** — Timelessness rubric for skill reviews
3. **Explicit negative triggers** — "Do NOT use for X" in descriptions

---

## NeoLabHQ/context-engineering-kit (GPL-3.0 — ideas only, re-expressed)

Source: https://github.com/NeoLabHQ/context-engineering-kit (mined under HATS-631).
GPL-licensed: we borrow *ideas* and cite *primary* sources, never lift text.

### Evidence-backed claims worth citing (primary sources)
- **Lost-in-the-middle / U-shaped attention** — LLMs recall start/end of context
  far better than the middle. Primary: Liu et al. 2023, *Lost in the Middle: How
  Language Models Use Long Contexts*, TACL 2024 (arXiv:2307.03172). This is the
  rationale behind HATS-620's `## Guardrails`-placed-early convention; wired into
  `docs/how-to-extend.md` and the `review-role` attention-placement audit axis.
- **Persuasion → compliance** — instruction framing (Authority/Commitment/Social
  Proof) roughly doubles compliance; avoid Liking/Reciprocity (breed sycophancy).
  Primary: Meincke et al. 2025. (Candidate for a prompt-engineering skill.)
- **"95% finding"** — token usage (~80%), tool-call count (~10%), model (~5%)
  explain most agent-performance variance (BrowseComp). (Candidate evidence for
  audit-reviewer / eval guidance.)

Full triage of all 14 borrowable patterns lives in the HATS-631 work log.
