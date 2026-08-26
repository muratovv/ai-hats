---
name: library-layer-split
description: Decide which library layer a component belongs to — core, usage, or ai-hats-dev. Use when creating, moving, or reviewing a rule, skill, trait or role, or when a component's trigger surface stops matching the layer it sits in.
license: MIT
---

# Library Layer Split

Below, `<LIB>` = `packages/ai-hats-library/src/ai_hats_library/`.

Every component has exactly one layer, and the layer is a property of the
**component, not of the bundle that composes it**. Resolution is name-based
across all roots (`find_component_dir`), so a trait in `ai-hats-dev/` composing
a skill in `usage/` is ordinary, not a workaround. Never move a component just
to sit next to its composer.

## The test — three steps, in order

Ask them top to bottom and stop at the first `yes`.

1. **Does the engine refuse to run without it?** `ai-hats init`, `ai-hats self
   init`, or a reflect pipeline breaks if it is absent → **`<LIB>/core/`**.
   System roles, base traits, global rules, foundational skills, pipelines.
2. **Would a consumer project ever compose it?** → **`<LIB>/usage/`**.
   The opinionated catalog: domain roles, `dev::` / `env::` traits, opt-in
   skills. "Some consumers, not all" is still a `yes` here.
3. **Otherwise only this repository composes it** → **`<LIB>/ai-hats-dev/`**.
   Its trigger surface names ai-hats' own source, or it exists in order to
   develop ai-hats.

Layer priority runs lowest → highest as `core` → `usage` → `ai-hats-dev`, so the
most specific layer wins a name collision. `core` and `usage` are REQUIRED for a
dir to be a library root; `ai-hats-dev` is optional.

## Reading step 3

The signal is the **trigger surface**, not the vocabulary. A component that says
"ai-hats" in prose but fires on any project is `usage/`; a component that never
says it but only ever fires here is `ai-hats-dev/`.

Concretely, step 3 is `yes` when the component names `src/ai_hats/…`,
`packages/ai-hats-library/…`, this repo's `CONTRIBUTING.md`, `docs/adr/…`, or
`scripts/ci-local.sh` **as a dependency rather than as an example**.

A guarded fast path is not a dependency. `rule-delivery-gate` hard-codes
`packages/ai-hats-library/src/ai_hats_library` and still lives in `usage/`,
because the hard-code is an `if [[ -d … ]]` branch with a package-resolve
fallback — it works in any project.

## Worked splits

| Component                                                    | Layer         | Why                                                                                         |
| ------------------------------------------------------------ | ------------- | ------------------------------------------------------------------------------------------- |
| `trait-base`, `hatrack`, reflect pipelines                   | `core`        | step 1 — the engine stops                                                                   |
| `skill-template`, `skill-optimization`, `retro-to-framework` | `usage`       | step 2 — any consumer authoring components wants them                                       |
| `rule-delivery-gate`, `skill-lint-gate`                      | `usage`       | step 2 — repo path is a guarded fast path                                                   |
| `maintainer-quality-gate`, `doc-protocol`, `worktree-venv`   | `ai-hats-dev` | step 3 — wired to this repo's gates and docs                                                |
| `rule_composition_value_contract`                            | `ai-hats-dev` | step 3 — names `CompositionResult` / `WrapRunner`                                           |
| `skill-engineer` trait                                       | `ai-hats-dev` | step 3 — injection is about *this* library; promote to `usage/` the day a consumer needs it |

## Anti-patterns

- **A component in `core/` whose body names `src/ai_hats/`.** Every consumer
  then pays always-on tokens for ai-hats internals. This is how
  `rule_composition_value_contract` rode `trait-agent` into 11 roles (HATS-1834).
- **Moving a component to follow its composer.** Cross-layer composition works;
  see the top of this file.
- **A universal pattern parked in `usage/`** because its first consumer happened
  to be one domain — consumers never discover it there.
- **Deciding the layer after writing the component.** The layer is a plan-stage
  answer; retrofitting it costs a rename across tests, docs and hard-coded paths.

## Checking your answer

`ai-hats list tokens <role>` before and after. A component that moved *out* of a
base trait must disappear from the roles that no longer carry it and stay in the
ones that do — if it vanished from both, the detach was too wide.

## See also

- `skill-template` — which component TYPE (rule / skill / trait) this should be.
  Answer that first; this skill only answers where it lives.
- `CONTRIBUTING.md#library-structure` — human mirror of this decision.

Source: PROP-037, from HATS-373 (pattern-matching to the nearest neighbour
instead of asking the question, paid for with a full plan rewrite). Third layer
and the demotion from rule to skill: HATS-1834.
