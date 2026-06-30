#!/usr/bin/env python3
"""HATS-660 — py-security-lint PostToolUse hook.

Edit-time security signal for Python: on a PostToolUse Edit|Write|MultiEdit of a
``.py`` file, run ruff's security ruleset (``--select S`` = flake8-bandit) on the
just-written file and forward any findings to the agent as a NON-BLOCKING
``hookSpecificOutput.additionalContext`` message. The audience is the agent: it
self-corrects before commit.

Contract (reuses the HATS-632 convention, PostToolUse variant): stdin = Claude
Code hook payload JSON; read ``.tool_input.file_path``; on findings ->
  exit 0 + {"hookSpecificOutput":{"hookEventName":"PostToolUse",
            "additionalContext":"<ruff findings>"}}
otherwise exit 0 with no stdout. A ``permissionDecision`` is NEVER emitted, so the
tool is never blocked.

Defense-in-depth, NOT a gate: this is the early, soft, Claude-only, single-file
layer; the project's CI/pre-commit lint stays the hard, comprehensive gate. We run
``--select S`` (usually off by default) so we ADD security coverage rather than
duplicating the project's general lint, and ``--isolated`` so the project's other
rules / config never leak in.

Zero network egress (stdlib only; shells out only to local ``ruff``). Fail-open:
any error, a missing ``ruff``, a non-``.py`` file, or an unparsable payload ->
exit 0 silently. Kill switch: ``AI_HATS_SECURITY_LINT_OFF=1`` -> immediate no-op.
Suppress an intentional finding inline with ``# noqa: S…`` (ruff honours it even
under ``--isolated``). Provider asymmetry: Claude consumes this; Gemini is a no-op.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

_KILL_SWITCH = "AI_HATS_SECURITY_LINT_OFF"


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0  # unparsable / empty -> fail-open allow

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py") or not os.path.isfile(file_path):
        return 0

    ruff = shutil.which("ruff")
    if not ruff:
        return 0  # ruff not installed -> no-op (the CI gate still covers it)

    try:
        proc = subprocess.run(
            [
                ruff, "check", "--isolated", "--select", "S",
                "--output-format", "concise", "--quiet", file_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return 0  # ruff crash / timeout -> fail-open

    findings = proc.stdout.strip()
    if not findings:
        return 0  # clean -> silent

    msg = (
        "dev_rule_secure_coding — ruff security (flake8-bandit `S`) findings on the "
        "file you just edited. Non-blocking; fix, or suppress an intentional one "
        "with an inline `# noqa: S…`:\n" + findings
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
