# Contributing to ai-hats-observe

`ai-hats-observe` is developed in the [ai-hats](https://github.com/muratovv/ai-hats)
monorepo under `packages/ai-hats-observe/`. Follow the repository-root contributing
guide for setup, branch, and commit conventions.

## The one rule that is specific to this package

`ai_hats_observe` must import **only** the Python standard library and
`ai_hats_core` — never the `ai_hats` integrator. This one-directional boundary
(ai-hats depends on ai-hats-observe, never the reverse) is what keeps the package
standalone-consumable. It is enforced by `tests/test_observe_boundary.py`; a new
import that breaks it fails that test.

Provider-specific parsing stays behind the `TranscriptParser` adapter: the
`AuditWriter` never grows a provider branch. A new surface ships a parser that
satisfies the protocol; the integrator injects it via the session's provider.

## Tests

```sh
pytest packages/ai-hats-observe/tests
```

`test_observe_standalone.py` proves session logging (trace + audit + metrics) on a
bare directory with `recovery=None` and no ai-hats; `test_observe_boundary.py` is
the static import-boundary guard.
