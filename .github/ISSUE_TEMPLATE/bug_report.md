---
name: Bug report
about: Something is broken or doesn't work as documented.
title: 'bug: <one-line summary>'
labels: bug
---

## Summary

A clear one-paragraph description of the bug.

## Reproduction

Minimal steps that reliably reproduce the bug:

1. `ai-hats ...`
2. `...`
3. Observed: ...
4. Expected: ...

If possible, include the commands as a copy-pasteable block.

## Environment

- `ai-hats --version`:
- Python: `python3 --version` →
- OS / shell:
- Provider (claude / gemini) and CLI version:
- Anything non-default in `ai-hats.yaml`:

## Logs

If the issue is reproducible, the contents of
`<ai_hats_dir>/sessions/runs/<id>/audit.md` and the failure tail of
`<id>/trace.log` are usually enough. **Redact** any absolute paths,
session IDs, and prompts before pasting — see
[CONTRIBUTING.md → What not to commit](../CONTRIBUTING.md#what-not-to-commit)
for the same redaction guidance.

## Additional context

Workarounds you have tried, related issues, hypotheses about the cause.
