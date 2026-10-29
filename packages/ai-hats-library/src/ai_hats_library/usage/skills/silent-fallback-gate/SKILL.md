---
name: silent-fallback-gate
description: >-
  PreToolUse hook — refuses an Edit or Write that adds an inert broad
  except. Use when a Python edit was blocked for a silent fallback.
ai_hats:
  runtime_hooks:
    PreToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/silent_fallback_gate.py
license: MIT
---

# silent-fallback-gate

Thin registration for a PreToolUse hook. On an Edit, Write or MultiEdit of a
`.py` file outside `tests/`, the hook refuses when the edit adds a broad
`except` whose body neither raises, calls, nor uses the bound exception.
`# silent-ok: <reason>` marks a deliberate one. The refusal names that choice.

The check is `violations()` in `hooks/silent_fallback_gate.py`. CI walks the
tree through `scripts/check_silent_fallback.py`, which calls the same function.
An edit that does not grow the set is silent, including a file that already
has one. A payload the hook cannot reconstruct is fail-open; the CI walk still
sees the file. Kill switch `AI_HATS_SILENT_FALLBACK_OFF=1`, exported.

## When to Use

Read this when an edit was blocked and the next step in the refusal is unclear.
Not a nudge: `py-security-lint` reports and lets the edit through.

## Completion

The refusal names the line and one of: report it, re-raise it, or mark
`# silent-ok: <reason>`.

**Validation scenario (RED).** An agent adds `except Exception: return []` to a
module. Without this hook the standing rule asked it not to, and the edit
landed; CI caught it at review. With the hook the edit is refused and the
reason names the mark.

## Anti-Patterns

- Refusing every edit of a file that already contains an inert handler.
- A second copy of the check in the CI script.
