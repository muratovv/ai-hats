# Contributing to ai-hats-wt

`ai-hats-wt` is developed in the [ai-hats](https://github.com/muratovv/ai-hats)
monorepo under `packages/ai-hats-wt/`. Follow the repository-root contributing
guide for setup, branch, and commit conventions.

## The one rule that is specific to this package

`ai_hats_wt` must import **only** the Python standard library, `filelock`, and
`ai_hats_core` — never the `ai_hats` integrator. This one-directional boundary
(ai-hats depends on ai-hats-wt, never the reverse) is what keeps the package
standalone-consumable. It is enforced by `tests/test_boundary.py`; a new import
that breaks it fails that test.

## Tests

```sh
pytest packages/ai-hats-wt/tests
```

`test_wt_standalone.py` proves the create → merge / discard lifecycle on a bare
git repo with no ai-hats; `test_boundary.py` is the static import-boundary guard.
