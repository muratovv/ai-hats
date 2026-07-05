"""HATS-632 — script-level behaviour of the tool-call-hygiene PreToolUse guard.

Per ``dev_rule_e2e_gate`` the guard is a pure-bash surface the unit suite cannot
meaningfully exercise. We invoke it as a real subprocess, feeding Claude Code
``PreToolUse`` payloads on stdin and asserting the **non-blocking** contract:
exit 0 + ``hookSpecificOutput.additionalContext`` on a covered command, and
NEVER a ``permissionDecision`` (so the command is never blocked or auto-approved).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import HOOK_PRE_TOOL_USE

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUARD = (
    REPO_ROOT
    / "library/core/skills/tool-call-hygiene/hooks/tool_call_hygiene_guard.sh"
)


def _run(command: str | None, *, env: dict | None = None, raw: str | None = None):
    if raw is not None:
        stdin = raw
    elif command is None:
        stdin = ""
    else:
        stdin = json.dumps(
            {
                "hook_event_name": HOOK_PRE_TOOL_USE,
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )
    base_env = os.environ.copy()
    base_env.pop("AI_HATS_TOOL_HYGIENE_OFF", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(GUARD)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=5,
        env=base_env,
    )


def _nudge(res) -> str | None:
    """Parse stdout as the hook JSON; return additionalContext text or None."""
    out = res.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    return data.get("hookSpecificOutput", {}).get("additionalContext")


@pytest.mark.integration
def test_pure_grep_nudges_to_grep_tool():
    res = _run("grep foo .")
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None, f"expected a nudge, got stdout={res.stdout!r}"
    assert "Grep" in ctx
    # Non-blocking contract: never emit a permissionDecision.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "cat a.txt | grep foo",       # covered leading token, but piped
        "grep foo . && echo done",    # chained with &&
        "find . -name x ; ls",        # chained with ;
        "grep foo $(ls)",             # command substitution
        "cat `which bash`",           # backtick subshell
        "grep foo file > out.txt",    # redirection
        "git log --oneline | head",   # non-covered leading token + pipe
        "make build",                 # build command, not covered
    ],
)
def test_compound_or_noncovered_gets_no_nudge(command):
    """Bias-to-allow: anything compound/redirected is legitimately Bash."""
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge for {command!r}: {res.stdout!r}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "command,tool",
    [
        ("ls -R", "Glob"),                       # recursive listing
        ("ls -laR /tmp", "Glob"),                # -R inside a flag cluster
        ("sed -i 's/a/b/' f.txt", "Edit"),       # in-place edit
        ("sed -i.bak s/a/b/ f.txt", "Edit"),     # in-place with backup suffix
        ("awk -i inplace '{print}' f.txt", "Edit"),
    ],
)
def test_conditional_covered_forms_nudge(command, tool):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    ctx = _nudge(res)
    assert ctx is not None and tool in ctx, f"{command!r} -> {res.stdout!r}"
    # Mutating commands (sed -i / awk -i) must NEVER be auto-approved.
    assert "permissionDecision" not in res.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    "command",
    [
        "ls -la",                  # non-recursive listing is fine
        "sed 's/a/b/' f.txt",      # stream sed (not in-place) is fine
        "awk '{print $1}' f.txt",  # stream awk is fine
    ],
)
def test_noncovered_command_forms_get_no_nudge(command):
    res = _run(command)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None, f"unexpected nudge: {res.stdout!r}"


# --- fail-safe edge cases ----------------------------------------------------


@pytest.mark.integration
def test_empty_payload_no_crash_no_nudge():
    res = _run(None)  # empty stdin (harness no-op / manual test)
    assert res.returncode == 0
    assert _nudge(res) is None


@pytest.mark.integration
def test_garbage_payload_fails_safe():
    res = _run(None, raw="this is not json {{{")
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None


@pytest.mark.integration
def test_non_bash_payload_gets_no_nudge():
    raw = json.dumps({"tool_input": {"file_path": "/tmp/x"}})
    res = _run(None, raw=raw)
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None


@pytest.mark.integration
def test_kill_switch_disables_hook():
    res = _run("grep foo .", env={"AI_HATS_TOOL_HYGIENE_OFF": "1"})
    assert res.returncode == 0, res.stderr
    assert _nudge(res) is None
