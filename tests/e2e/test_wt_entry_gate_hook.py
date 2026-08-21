"""e2e (HATS-1556)

flow:   an agent attempting to enter or create a worktree directly via tool call
cmds:
    # agent invoking EnterWorktree tool call directly
    ai-hats wt create task/probe
expect: direct worktree entry tool call is denied with instructions to use ai-hats CLI
why:    worktree creation and entry must be routed through ai-hats CLI commands
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/core/skills/worktree-isolation"
    "/hooks/wt_entry_gate.py"
)


def _decide(payload: dict, *, env_extra: dict[str, str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AI_HATS_")}
    env.update(env_extra or {})
    res = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)["hookSpecificOutput"] if res.stdout.strip() else {}


def test_creating_a_worktree_is_denied_with_the_ai_hats_recipe():
    out = _decide({"tool_name": "EnterWorktree", "tool_input": {}})

    assert out["permissionDecision"] == "deny"
    assert "ai-hats wt create" in out["permissionDecisionReason"]


def test_entering_an_existing_worktree_is_denied_naming_the_path():
    out = _decide({"tool_name": "EnterWorktree", "tool_input": {"path": "/tmp/wt-42"}})

    assert out["permissionDecision"] == "deny"
    assert "cd /tmp/wt-42" in out["permissionDecisionReason"]


def test_the_agy_payload_shape_is_denied_too():
    """Non-Claude surfaces deliver the arguments under toolCall.args — and the
    SURFACE translates before the script is spawned (HATS-1776).

    Driven through the bridge on purpose: the script's own dual reading is gone,
    because two readings of one payload are two truths. What must still hold is
    the chain — an agy-shaped call reaches the same verdict as a Claude one.
    """
    from ai_hats_agy.claude_hook_adapter import to_claude_payload

    out = _decide(
        to_claude_payload({"toolCall": {"name": "EnterWorktree", "args": {"path": "/tmp/wt-7"}}})
    )
    assert out["permissionDecision"] == "deny"
    assert "cd /tmp/wt-7" in out["permissionDecisionReason"]


def test_the_kill_switch_disables_the_gate():
    assert _decide({"tool_input": {}}, env_extra={"AI_HATS_WT_ENTRY_OFF": "1"}) == {}


def test_an_unparsable_payload_fails_open():
    """Documented fail-open — pinned so it cannot flip to a blanket deny unnoticed."""
    res = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert res.returncode == 0
    assert res.stdout.strip() == ""
