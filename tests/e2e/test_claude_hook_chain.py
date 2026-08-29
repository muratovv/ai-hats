"""e2e (HATS-1868)

flow:   an operator runs a claude role whose skills compose several gates on one
        tool, and expects them to behave as ONE chain — a shared budget, joined
        advice, one verdict
cmds:
    sh -c "$DISPATCHER_COMMAND"   # the string a settings.json entry holds
expect: a later gate overrides an earlier one, advice from both reaches the
        model, a consent ticket arrives with its rewrite attached, and a chain
        that overruns its budget refuses while naming the bound to raise
why:    the harness runs each entry on its own — no shared deadline, no joined
        advice, and a gate killed by its per-hook timeout lets the call through
        (poc-hook-delivery.md M7). A chain is the thing owning execution buys
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from _helpers.hook_chain import run_claude_dispatch
from _helpers.sessions import stand_in_session

SESSION_ID = "sid-claude-chain"
OFF_LIMITS = "/etc/passwd"


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _emit(**spoken) -> str:
    doc = json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **spoken}})
    return f"cat >/dev/null\nprintf '%s' '{doc}'\n"


def _session(tmp_path: Path, rows: list[dict]) -> SimpleNamespace:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    (cache / "hooks.json").write_text(
        json.dumps({"version": 1, "session": {"id": SESSION_ID}, "hooks": {"PreToolUse": rows}}),
        encoding="utf-8",
    )
    env = stand_in_session(dict(os.environ), project, SESSION_ID, provider="claude")
    env |= {
        "AI_HATS_SESSION_CACHE_DIR": str(cache),
        "AI_HATS_PROJECT_DIR": str(project),
        "AI_HATS_PYTHON": sys.executable,
    }
    env.pop("AI_HATS_GATE_BROKEN_ACK", None)
    return SimpleNamespace(project=project, env=env, cache=cache)


def _mirror(tmp_path: Path) -> Path:
    root = tmp_path / "cache" / "plugin" / "skills" / "guards"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _row(script: Path, tag: str, matcher: str = "Bash") -> dict:
    return {"command": str(script), "matcher": matcher, "tag": f"ai-hats:{tag}"}


@pytest.fixture
def chain(tmp_path: Path) -> SimpleNamespace:
    """An audit that advises, then a guard that refuses one path."""
    mirror = _mirror(tmp_path)
    audit = _script(mirror / "audit.sh", "#!/bin/sh\n" + _emit(additionalContext="prefer Read"))
    guard = _script(
        mirror / "guard.sh",
        "#!/bin/sh\n"
        + f'if grep -q "{OFF_LIMITS}" 2>/dev/null; then\n'
        + "  printf '%s' '"
        + json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{OFF_LIMITS} is off limits",
                }
            }
        )
        + "'\nfi\n",
    )
    return _session(tmp_path, [_row(audit, "audit"), _row(guard, "guard")])


def _spoken(done) -> dict:
    assert done.stdout.strip(), f"the dispatcher said nothing at all: {done.stderr!r}"
    return json.loads(done.stdout)["hookSpecificOutput"]


def test_a_later_gate_overrides_an_earlier_one(chain) -> None:
    """The single-hook blind spot: the audit allowed, and the verdict that
    reaches claude is still the guard's (HATS-1113/1253)."""
    done = run_claude_dispatch(
        chain.project, chain.env, tool="Bash", tool_input={"command": f"cat {OFF_LIMITS}"}
    )

    spoken = _spoken(done)
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert OFF_LIMITS in spoken["permissionDecisionReason"]


def test_advice_from_a_gate_before_the_objector_survives_the_refusal(chain) -> None:
    """The dialect promises this surface carries advice; dropping it on a deny
    would make that promise false for every gate the objector ran behind."""
    done = run_claude_dispatch(
        chain.project, chain.env, tool="Bash", tool_input={"command": f"cat {OFF_LIMITS}"}
    )

    assert _spoken(done)["additionalContext"] == "prefer Read", done.stdout


def test_the_control_an_allowed_command_passes_with_the_advice(chain) -> None:
    """Without this, the refusals above are indistinguishable from a chain that
    refuses everything."""
    done = run_claude_dispatch(
        chain.project, chain.env, tool="Bash", tool_input={"command": "echo hello"}
    )

    spoken = _spoken(done)
    assert "permissionDecision" not in spoken, done.stdout
    assert spoken["additionalContext"] == "prefer Read"


def test_a_consent_ticket_arrives_with_its_rewrite(tmp_path: Path) -> None:
    """`safety_gate.py` mints a nonce and rewrites the command with it. The
    question and the rewrite are ONE capability: asked without it, the human
    approves the original line."""
    mirror = _mirror(tmp_path)
    ticket = _script(
        mirror / "ticket.sh",
        "#!/bin/sh\n"
        + _emit(
            permissionDecision="ask",
            permissionDecisionReason="confirm this",
            updatedInput={"command": "echo REWRITTEN_BY_TICKET"},
        ),
    )
    session = _session(tmp_path, [_row(ticket, "safety-guard")])

    done = run_claude_dispatch(
        session.project, session.env, tool="Bash", tool_input={"command": "echo ORIGINAL"}
    )

    spoken = _spoken(done)
    assert spoken["permissionDecision"] == "ask", done.stdout
    assert spoken["updatedInput"] == {"command": "echo REWRITTEN_BY_TICKET"}, (
        "the question arrived without its ticket — approving it would run the original"
    )


def test_a_chain_that_overruns_its_budget_refuses_and_names_the_bound(tmp_path: Path) -> None:
    """M7 inverted. The harness supports a per-hook `timeout` and lets the call
    through when it fires; one budget for the call refuses instead."""
    mirror = _mirror(tmp_path)
    slow = _script(mirror / "slow.sh", "#!/bin/sh\ncat >/dev/null\nsleep 5\n")
    session = _session(tmp_path, [_row(slow, "slow")])
    session.env["AI_HATS_HOOK_TIMEOUT_S"] = "1"

    done = run_claude_dispatch(
        session.project, session.env, tool="Bash", tool_input={"command": "echo hi"}, timeout=30
    )

    spoken = _spoken(done)
    assert spoken["permissionDecision"] == "deny", done.stdout
    assert "AI_HATS_HOOK_TIMEOUT_S" in spoken["permissionDecisionReason"], (
        f"a budget refusal must name the bound to raise:\n{done.stdout!r}"
    )


def test_a_gate_bound_to_another_tool_stays_out_of_this_call(tmp_path: Path) -> None:
    """Under one dispatcher entry the matching moves from the harness to us, so
    a matcher that over-reaches would put every gate on every call."""
    mirror = _mirror(tmp_path)
    ran = tmp_path / "ran"
    edits = _script(mirror / "edits.sh", f"#!/bin/sh\ncat >/dev/null\ntouch {ran}\n")
    session = _session(tmp_path, [_row(edits, "edits", matcher="Edit|Write|MultiEdit")])

    run_claude_dispatch(
        session.project, session.env, tool="Bash", tool_input={"command": "echo hi"}
    )
    assert not ran.exists(), "an Edit gate fired on a Bash call"

    run_claude_dispatch(
        session.project, session.env, tool="Write", tool_input={"file_path": "/tmp/x"}
    )
    assert ran.exists(), "the same gate did not fire on the tool it guards"
