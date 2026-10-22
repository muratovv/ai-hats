"""The harness task list is refused, and a neighbouring tool is not."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOK = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
    / "harness_task_gate.py"
)


def _run(tool: str, *, off: bool = False) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin"}
    if off:
        env["AI_HATS_HARNESS_TASKS_OFF"] = "1"
    return subprocess.run(
        ["python3", str(HOOK)],
        input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {}}),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )


def test_task_create_is_denied_and_names_rack() -> None:
    result = _run("TaskCreate")
    assert result.returncode == 0, result.stderr
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "rack transition" in reason
    assert "AI_HATS_HARNESS_TASKS_OFF" in reason


def test_a_bash_payload_is_silent() -> None:
    result = _run("Bash")
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_the_supervisor_switch_allows_the_tool() -> None:
    result = _run("TaskList", off=True)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
