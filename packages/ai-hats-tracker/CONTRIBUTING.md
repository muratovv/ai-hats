# Contributing to ai-hats-tracker

`ai-hats-tracker` is developed in the [ai-hats](https://github.com/muratovv/ai-hats)
monorepo under `packages/ai-hats-tracker/`. Follow the repository-root contributing
guide for setup, branch, and commit conventions.

## The one rule that is specific to this package

`ai_hats_tracker` must import **only** the Python standard library, `pydantic`,
`PyYAML`, `filelock`, and `ai_hats_core` — never the `ai_hats` integrator and
never `ai_hats_wt`. This one-directional boundary (ai-hats depends on
ai-hats-tracker, never the reverse) is what keeps the package
standalone-consumable. It is enforced by `tests/test_boundary.py`; a new import
that breaks it fails that test.

## Tests

```sh
pytest packages/ai-hats-tracker/tests
```

`test_tracker_standalone.py` proves the create → transition → done lifecycle on a
bare directory with no ai-hats; `test_boundary.py` is the static import-boundary
guard.
