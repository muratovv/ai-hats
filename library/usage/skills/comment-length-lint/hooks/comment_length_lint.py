#!/usr/bin/env python3
"""HATS-842 — comment-length-lint PostToolUse hook.

Edit-time over-commenting signal: on a PostToolUse Edit|Write|MultiEdit of a
``.py`` file, flag oversized comment blocks and docstrings on the just-written
file and forward them to the agent as a NON-BLOCKING
``hookSpecificOutput.additionalContext`` nudge. The audience is the agent: it
trims before commit. Backstop for ``dev_rule_comment_discipline`` (the few-shot
guide is the primary defense); this catches what slips past it.

What trips it (all env-tunable, all advisory):
  * a run of > ``AI_HATS_COMMENT_MAX_LINES`` (default 3) consecutive standalone
    ``#`` comment lines — the HATS-837 4-line DI-wiring comment shape;
  * a docstring over ``AI_HATS_DOCSTRING_MAX_LINES`` (default 10) lines OR
    ``AI_HATS_DOCSTRING_MAX_CHARS`` (default 700) chars — the HATS-837
    multi-paragraph essay shape. Tuned so healthy contract docstrings
    (≈9 lines / ≈470 chars in this repo) stay silent.

Contract (PostToolUse variant of the HATS-632/660 convention): stdin = Claude
Code hook payload JSON; read ``.tool_input.file_path``; on findings -> exit 0 +
``additionalContext``; else exit 0, no stdout. NEVER emits a
``permissionDecision``, so the tool is never blocked.

Zero network egress (stdlib only). Fail-open: any error, a non-``.py`` file, an
unparsable payload, or a syntax error in the target -> exit 0 silently. Kill
switch: ``AI_HATS_COMMENT_LINT_OFF=1``. Provider asymmetry: Claude consumes this;
Gemini is a no-op.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import tokenize

_KILL_SWITCH = "AI_HATS_COMMENT_LINT_OFF"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _comment_run_findings(src: str, max_lines: int) -> list[str]:
    """Lines of every run of > max_lines consecutive standalone `#` comments."""
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    standalone = [
        t.start[0]
        for t in toks
        if t.type == tokenize.COMMENT and t.line[: t.start[1]].strip() == ""
    ]
    out: list[str] = []
    start = prev = None
    count = 0
    for ln in sorted(standalone):
        if prev is not None and ln == prev + 1:
            count += 1
        else:
            if count > max_lines:
                out.append(f"  L{start}: comment block of {count} lines (> {max_lines})")
            start, count = ln, 1
        prev = ln
    if count > max_lines:
        out.append(f"  L{start}: comment block of {count} lines (> {max_lines})")
    return out


def _docstring_findings(src: str, max_lines: int, max_chars: int) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        if not (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            continue
        nlines = (first.end_lineno or first.lineno) - first.lineno + 1
        nchars = len(first.value.value)
        if nlines > max_lines or nchars > max_chars:
            where = getattr(node, "name", "<module>")
            out.append(
                f"  L{first.lineno}: docstring on {where!r} is {nlines} lines / "
                f"{nchars} chars (> {max_lines} lines or {max_chars} chars)"
            )
    return out


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        return 0
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py") or not os.path.isfile(file_path):
        return 0
    try:
        with open(file_path, encoding="utf-8") as fh:
            src = fh.read()
    except OSError:
        return 0

    findings = _comment_run_findings(src, _env_int("AI_HATS_COMMENT_MAX_LINES", 3))
    findings += _docstring_findings(
        src,
        _env_int("AI_HATS_DOCSTRING_MAX_LINES", 10),
        _env_int("AI_HATS_DOCSTRING_MAX_CHARS", 700),
    )
    if not findings:
        return 0

    msg = (
        "dev_rule_comment_discipline — oversized comments/docstrings on the file "
        "you just edited. Non-blocking; trim to the one-line WHY (or move long "
        "rationale to an ADR / task card), or tune the threshold env vars if this "
        "is a genuine contract:\n" + "\n".join(findings)
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": msg,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
