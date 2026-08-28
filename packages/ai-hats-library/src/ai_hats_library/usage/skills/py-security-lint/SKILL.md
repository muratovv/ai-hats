---
name: py-security-lint
description: PostToolUse hook — reports the project's own ruff security (`S`) findings on every `.py` you Edit/Write, non-blockingly; fail-open, no manual invocation.
ai_hats:
  runtime_hooks:
    PostToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/py_security_lint.py
license: MIT
---

# py-security-lint

Thin registration shell for a PostToolUse runtime-hook: after every agent
`Edit`/`Write`/`MultiEdit` of a `.py`, `ruff check` runs on that file under the
**project's own configuration** and the flake8-bandit (`S`) findings are
forwarded to the agent via non-blocking `additionalContext`. Reporting under the
project's config rather than a forced `--select S` is what keeps the nudge equal
to what the gate would refuse — a forced selection re-enables rules the project
excluded on purpose, and a message that is mostly noise gets scrolled past.
Defense-in-depth — the project's CI lint stays the gate. Fail-open,
kill switch `AI_HATS_SECURITY_LINT_OFF=1`; full contract in the hook header:
`hooks/py_security_lint.py`.
