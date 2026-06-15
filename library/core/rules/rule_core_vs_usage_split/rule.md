# Rule: Core vs Usage Library Split

When creating a new component (rule / skill / trait), the first question is:
**is this universal or project-specific?**

- **Universal** — any project on ai-hats would want this. Lives in `library/core/`.
  Attached to a base trait (`trait-base`, `trait-agent`, …).
- **Project-specific** — only the ai-hats codebase itself (or a similar single
  domain) needs this. The reusable rule/skill still goes in `library/core/`
  if the **concept** transfers (e.g., `dev_rule_e2e_gate`), but it's
  **NOT attached to a base trait** — instead, attach via a dedicated trait
  in `library/usage/traits/<role>-<discipline>/`, then wire the trait into
  a role via `library/usage/roles/<role>/config.yaml`.

## Decision tree

```
New rule / skill / trait
        │
        ▼
 Universal? (any ai-hats project would benefit?)
        │
   ┌────┴────┐
  yes        no
   │         │
   ▼         ▼
 core/    Reusable concept?
          (e.g., a constraint that other projects would adopt)
              │
         ┌────┴────┐
        yes        no
         │         │
         ▼         ▼
       core/   usage/
       (rule)  (trait + role wiring)
       +
       dedicated usage/ trait that bundles it
       +
       role-wiring in usage/roles/
```

## Anti-patterns

- **Attaching project-specific rules to `trait-agent` / `trait-base`** — pollutes universal traits with narrow concerns. Other ai-hats consumers inherit irrelevant rules.
- **Putting universal patterns in `usage/`** — buries reusable concepts under project-specific folders; consumers won't discover them.
- **Skipping the dedicated usage/ trait** for project-specific rules — putting the project-specific rule directly into a usage role bypasses the trait layer that makes the bundling reusable.

## Worked example

**HATS-373 (E2E gate, 2026-05).** First draft attached the new
`dev_rule_e2e_gate` (narrow trigger: CLI/shell/pip ai-hats codebase) to
`trait-agent` alongside `backlog_discipline` and `tool_call_hygiene`. The rule
itself was reusable in concept (other projects with CLI surface might want
the same gate), but its **specific trigger surface** was ai-hats-internal.

User redirect: "выделим отдельный trait `ai-hats-maintainer`" — full rewrite
of the plan. The corrected split:

- `library/core/rules/dev_rule_e2e_gate/` — rule (reusable concept).
- `library/usage/traits/ai-hats-maintainer/` — bundles the rule + project-specific framing.
- Roles like `maintainer` opt in via composition; other roles don't.

## Source

PROP-037 (accepted). Class of error: pattern-matching to nearest neighbour
without the universal-vs-specific check. Cheap pre-check prevents costly plan
rewrites.

## See also

- `skill-engineer` trait — owns the component-type decision (rule / skill / trait).
- `CONTRIBUTING.md#library-structure-core-vs-usage` — human mirror of this rule.
