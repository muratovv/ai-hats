---
name: comment-length-lint
description: PostToolUse hook — flags oversized comment blocks and docstrings on every `.py` you Edit/Write and forwards them non-blockingly; fail-open, no manual invocation.
ai_hats:
  runtime_hooks:
    PostToolUse:
      - matcher: Edit|Write|MultiEdit
        script: hooks/comment_length_lint.py
---

# comment-length-lint

Thin registration shell for a PostToolUse runtime-hook: after every agent
`Edit`/`Write`/`MultiEdit` of a `.py`, oversized comment blocks (> 3 consecutive
`#` lines) and bloated docstrings (> 10 lines or > 700 chars) on the file are
forwarded to the agent via non-blocking `additionalContext`. Backstop for
`dev_rule_comment_discipline` — the few-shot guide is the primary defense; this
catches the HATS-837 shapes that slip past it. A deliberate long contract
suppresses a single finding with `# noqa: comment-length` (in the block, on the
`def`/`class` line, or inside a module docstring). Fail-open, kill switch
`AI_HATS_COMMENT_LINT_OFF=1`; thresholds tune via `AI_HATS_COMMENT_MAX_LINES` /
`AI_HATS_DOCSTRING_MAX_LINES` / `AI_HATS_DOCSTRING_MAX_CHARS`. Full contract in
the hook header: `hooks/comment_length_lint.py`.
