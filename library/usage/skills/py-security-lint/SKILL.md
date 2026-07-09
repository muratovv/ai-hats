---
name: py-security-lint
description: PostToolUse hook — runs ruff security rules (`--select S`) on every `.py` you Edit/Write and forwards findings non-blockingly; fail-open, no manual invocation.
ai_hats:
  runtime_hooks:
    PostToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/py_security_lint.py
license: MIT
---

# py-security-lint

Thin registration shell for a PostToolUse runtime-hook: after every agent
`Edit`/`Write`/`MultiEdit` of a `.py`, ruff's flake8-bandit security rules
(`ruff check --isolated --select S`) run on the file and any findings are
forwarded to the agent via non-blocking `additionalContext`. Defense-in-depth —
the project's CI lint stays the gate. Fail-open, kill switch
`AI_HATS_SECURITY_LINT_OFF=1`; full contract in the hook header:
`hooks/py_security_lint.py`.
