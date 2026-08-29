"""e2e (HATS-1873)

flow:   an agent launching a shell command with no upper bound on its lifetime
cmds:
    until grep -qE "=+ .*(passed|failed|error)" /tmp/unit2.log 2>/dev/null; do :; done
expect: the PreToolUse guard denies unbounded loops and background launches,
        nudges on unbounded installs, stays silent on commands that end
why:    the harness bounds the CALL, not the process, and the session-end
        reaper never runs in a session that stays open
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
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/command-lifetime"
    / "hooks/pre_bash_lifetime_guard.sh"
)

#: Verbatim from the incident. Positive control for every silence assertion
#: below: a broken guard is silent exactly like a satisfied one.
INCIDENT_CMD = 'until grep -qE "=+ .*(passed|failed|error)" /tmp/unit2.log 2>/dev/null; do :; done'


def _run(command: str | None, *, background: bool = False, env: dict | None = None, raw: str | None = None):
    if raw is not None:
        stdin = raw
    elif command is None:
        stdin = ""
    else:
        stdin = json.dumps(
            {
                "hook_event_name": HOOK_PRE_TOOL_USE,
                "tool_name": "Bash",
                "tool_input": {"command": command, "run_in_background": background},
            }
        )
    base_env = os.environ.copy()
    base_env.pop("AI_HATS_LIFETIME_ACK", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(GUARD)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        env=base_env,
    )


def _out(res) -> dict:
    """Parse stdout as the hook JSON; {} when the guard stayed silent."""
    text = res.stdout.strip()
    if not text:
        return {}
    return json.loads(text)["hookSpecificOutput"]


def _decision(res) -> str | None:
    return _out(res).get("permissionDecision")


def _reason(res) -> str:
    return _out(res).get("permissionDecisionReason", "")


def _nudge(res) -> str | None:
    return _out(res).get("additionalContext")


# --- S2: the unbounded loop is refused ---------------------------------------


def test_incident_command_is_denied():
    res = _run(INCIDENT_CMD)
    assert res.returncode == 0
    assert _decision(res) == "deny"


@pytest.mark.parametrize(
    "alternative",
    ["timeout", "counter", "Monitor"],
    ids=["names-timeout", "names-counter", "names-monitor"],
)
def test_refusal_names_the_bounded_alternative(alternative):
    """A4 — a refusal the agent cannot act on just costs a turn."""
    assert alternative in _reason(_run(INCIDENT_CMD))


@pytest.mark.parametrize(
    "cmd",
    [
        "while true; do :; done",
        "until test -f /tmp/flag; do sleep 1; done",
        "while ! curl -sf localhost:8080; do sleep 2; done",
    ],
    ids=["while-true", "until-sleep", "poll-endpoint"],
)
def test_unbounded_loops_denied_with_or_without_sleep(cmd):
    """Supervisor scope: 'любой цикл без ограничителя' — sleep is not a bound."""
    assert _decision(_run(cmd)) == "deny"


# --- S3: loops that already end are left alone -------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "timeout 300 bash -c 'until grep -q done /tmp/run.log; do sleep 5; done'",
        "while read -r line; do echo \"$line\"; done < /tmp/input.txt",
        'while IFS= read -r f; do rm "$f"; done < list.txt',
        "i=0; while [ $i -lt 10 ]; do i=$((i+1)); done",
        "for f in *.py; do ruff check $f; done",
    ],
    ids=["timeout-wrapped", "read-eof", "read-ifs", "counter", "for-list"],
)
def test_bounded_loops_pass_silently(cmd):
    assert _run(cmd).stdout.strip() == ""


def test_silence_on_bounded_loops_is_not_a_broken_guard():
    """Positive control for the test above — the same guard still refuses."""
    assert _decision(_run(INCIDENT_CMD)) == "deny"


def test_a_loop_quoted_in_prose_is_not_a_loop_being_run():
    """HATS-1819 — recording this incident in a work log must not be refused."""
    cmd = f"rack transition HATS-1873 --log 'the runaway was: {INCIDENT_CMD}'"
    assert _run(cmd).stdout.strip() == ""


# --- S4: the unbounded background launch is refused --------------------------


def test_background_without_timeout_denied():
    assert _decision(_run("python train.py", background=True)) == "deny"


def test_background_with_timeout_passes():
    assert _run("timeout 3600 python train.py", background=True).stdout.strip() == ""


def test_background_refusal_names_the_hatch():
    """A6 — a deny that does not name a working way past it strands the agent."""
    assert "AI_HATS_LIFETIME_ACK" in _reason(_run("python train.py", background=True))


# --- S5: long-running commands get advice, never a refusal -------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "pip install -r requirements.txt",
        "npm install",
        "brew install coreutils",
        "curl -sSL https://example.com/big.tar.gz -o /tmp/big.tar.gz",
        "git clone https://github.com/example/repo.git",
        "docker build -t app .",
    ],
    ids=["pip", "npm", "brew", "curl", "git-clone", "docker-build"],
)
def test_long_commands_are_nudged_never_denied(cmd):
    res = _run(cmd)
    assert _decision(res) is None, "trigger C must never block"
    assert _nudge(res), "trigger C must say something"


def test_long_command_with_timeout_is_silent():
    assert _run("timeout 600 pip install -r requirements.txt").stdout.strip() == ""


def test_test_runners_are_left_to_the_hygiene_guard():
    """Two nudges about one pytest run is how a channel gets tuned out."""
    assert _run("pytest tests/ -q").stdout.strip() == ""


# --- S6: fail-open, and the hatch --------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "not json at all", "{}", '{"tool_input": {}}'],
    ids=["empty", "garbage", "no-tool-input", "no-command"],
)
def test_unreadable_payload_fails_open(raw):
    res = _run(None, raw=raw)
    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_hatch_relaxes_the_refusal():
    res = _run(INCIDENT_CMD, env={"AI_HATS_LIFETIME_ACK": "1"})
    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_taking_the_hatch_is_recorded():
    """dev_rule_silent_fallback — passing a gate is allowed, passing it silently is not."""
    res = _run(INCIDENT_CMD, env={"AI_HATS_LIFETIME_ACK": "1"})
    assert "AI_HATS_LIFETIME_ACK" in res.stderr


# --- ordinary commands stay silent -------------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "git status --porcelain",
        "cat /tmp/foo.txt | head -20",
        "rack ls --parent HATS-491",
        "./.venv/bin/python -m pytest tests/ -q && git commit -m 'x'",
    ],
    ids=["ls", "git-status", "pipe", "rack", "chained"],
)
def test_ordinary_commands_pass_silently(cmd):
    assert _run(cmd).stdout.strip() == ""
