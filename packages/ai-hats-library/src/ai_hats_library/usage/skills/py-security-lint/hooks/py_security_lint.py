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
layer; the project's CI/pre-commit lint stays the hard, comprehensive gate.

HATS-1591: the run uses the PROJECT'S ruff configuration and keeps the ``S``
findings out of its output. It used to force ``--isolated --select S``, which
reports every rule in the family — including the ones a project has deliberately
excluded — so a repo that spawns processes by profession got 20 lines of
``S603``/``S607`` on every edit and learned to scroll past the whole message.
Both CLI forms defeat a declared exception (measured: ``--select S`` and
``--extend-select S`` each re-enable an ``ignore``d rule), so the project's own
configuration is the only invocation that reports what the gate would refuse.

Zero network egress (stdlib only; shells out only to local ``ruff``). Fail-open:
any error, a missing ``ruff``, a non-``.py`` file, or an unparsable payload ->
exit 0 silently. Kill switch: ``AI_HATS_SECURITY_LINT_OFF=1`` -> immediate no-op.
Suppress an intentional finding inline with ``# noqa: S…`` (ruff honours it even
under ``--isolated``). Provider asymmetry: Claude consumes this; Gemini is a no-op.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# The hooks are stdlib-only, so the journal arrives as a flattened sibling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_KILL_SWITCH = "AI_HATS_SECURITY_LINT_OFF"

# `path:line:col: S### message` — the flake8-bandit family, and only it.
_SECURITY_CODE = re.compile(r":\s*S\d+\s")


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook="py_security_lint.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="py_security_lint.py")
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not file_path.endswith(".py") or not os.path.isfile(file_path):
        return 0

    ruff = shutil.which("ruff")
    if not ruff:
        return 0  # ruff not installed -> no-op (the CI gate still covers it)

    try:
        proc = subprocess.run(
            [ruff, "check", "--output-format", "concise", "--quiet", file_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        # A linter that always crashes is a linter that always passes (HATS-1373).
        journal_bypass("fail-open", f"ruff crash/timeout: {exc!r}", hook="py_security_lint.py")
        return 0

    findings = "\n".join(
        line for line in proc.stdout.splitlines() if _SECURITY_CODE.search(line)
    ).strip()
    if not findings:
        return 0  # clean, or only non-security findings -> silent

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
