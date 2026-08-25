# Rule: Core vs Usage Library Split

Below, `<LIB>` = `packages/ai-hats-library/src/ai_hats_library/`.

When creating a new component (rule / skill / trait), the first question is:
**is this universal or project-specific?**

- **Universal** — any project on ai-hats would want this. Lives in `<LIB>/core/`.
  Attached to a base trait (`trait-base`, `trait-agent`, …).
- **Project-specific** — only the ai-hats codebase itself (or a similar single
  domain) needs this. The reusable rule/skill still goes in `<LIB>/core/`
  if the **concept** transfers (e.g., `dev_rule_e2e_gate`), but it's
  **NOT attached to a base trait** — instead, attach via a dedicated trait
  in `<LIB>/usage/traits/<role>-<discipline>/`, then wire the trait into
  a role via `<LIB>/usage/roles/<role>/config.yaml`.

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

- **Attaching a project-specific component to a `core/` trait** — pollutes universal traits with narrow concerns; other ai-hats consumers inherit them. This holds for **any component type** (a skill whose triggers hard-code this repo's paths pollutes exactly as a rule does) and for **any trait under `core/`**, not only `trait-agent` / `trait-base`.
- **Putting universal patterns in `usage/`** — buries reusable concepts under project-specific folders; consumers won't discover them.
- **Skipping the dedicated usage/ trait** for project-specific rules — putting the project-specific rule directly into a usage role bypasses the trait layer that makes the bundling reusable.

Shipped shape of the split: `dev_rule_e2e_gate` is a `core/` rule (the concept
transfers) bundled by the `usage/` trait `ai-hats-maintainer` (its trigger
surface does not), which roles opt into.

Source: PROP-037, from HATS-373 — pattern-matching to the nearest neighbour
instead of asking universal-vs-specific, paid for with a full plan rewrite.

## See also

- `skill-engineer` trait — owns the component-type decision (rule / skill / trait).
- `CONTRIBUTING.md#library-structure-core-vs-usage` — human mirror of this rule.
