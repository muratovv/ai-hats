<!--
Thanks for the contribution! Please skim CONTRIBUTING.md if you haven't
yet — particularly the "What not to commit" section.
-->

## Summary

What this PR changes, in one or two sentences. Reference the backlog ID
if applicable (e.g. `Closes HATS-NNN`).

## Why

The problem this solves, or the design rationale if the change is not
self-evident from the diff.

## Test plan

- [ ] `pytest tests/` passes locally.
- [ ] `ruff check .` is clean.
- [ ] Pre-commit privacy hook did not need an `AI_HATS_PRIVACY_ACK=1`
      override, or if it did — the reason is explained in the commit body.
- [ ] Manual smoke check: (describe).

## Breaking changes

- [ ] None.
- [ ] CLI surface changed: (describe migration).
- [ ] `ai-hats.yaml` schema changed: (describe migration).

## Notes for the reviewer

Anything specific you want a second pair of eyes on. Edge cases you are
unsure about. Trade-offs you made.
