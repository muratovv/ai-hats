# Behavior experiments (A/B)

Reusable infra for proving that a library-component edit (skill / rule / trait
wording) changes subagent behavior. Terms and structure: `docs/glossary.md` →
"Behavior experiment (A/B)"; authoring guide: `docs/how-to-experiments.md`.

- `_lib/` — shared runner scripts (prepare / run / collect / report / clean)
- `<name>/` — one experiment: `arms/`, `scenario/`, `score/`, `runs/` (gitignored)
- `smoke/` — trivial experiment used to verify the infra itself
