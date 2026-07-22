"""E2E integration test for Agy surface worktree-isolation write guard (HATS-1102).

Verifies that:
1. AgyProvider materializes the wt_gate.py hook script into .agy/skills/worktree-isolation/hooks/.
2. Running the materialized wt_gate.py script against an Edit/Write payload targeting a code/config file in the MAIN checkout emits permissionDecision == 'deny'.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats_agy.provider import AgyProvider

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.mark.integration
def test_agy_materializes_and_enforces_wt_gate_in_main_checkout(tmp_path: Path) -> None:
    # Set up a real main checkout repo
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@t.io")
    _git(main, "config", "user.name", "t")
    (main / "main_code.py").write_text("print('hello')\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")

    # Compose maintainer role (includes worktree-isolation skill) and materialize for agy
    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer")
    provider = AgyProvider()
    provider.materialize_runtime_skills(main, result, "sid-agy-gate")
    provider.ensure_runtime_hooks(main, result)

    settings_file = main / ".gemini" / "settings.json"
    assert settings_file.is_file(), ".gemini/settings.json must be created"
    settings_data = json.loads(settings_file.read_text())
    pre_tool_hooks = settings_data.get("hooks", {}).get("PreToolUse", [])
    assert any(
        "wt_gate.py" in h.get("command", "") and "Create" in h.get("matcher", "")
        for h in pre_tool_hooks
        if isinstance(h, dict)
    ), "wt_gate.py PreToolUse matcher in agy settings.json must include Create"

    hook_script = main / ".agy" / "skills" / "worktree-isolation" / "hooks" / "wt_gate.py"
    assert hook_script.is_file(), "wt_gate.py must be materialized in .agy/skills/"

    # Test payload targeting code file in MAIN checkout
    payload = json.dumps({
        "hook_event_name": HOOK_PRE_TOOL_USE,
        "tool_name": "Write",
        "tool_input": {"file_path": str(main / "main_code.py")},
        "cwd": str(main),
    })

    env = os.environ.copy()
    env.pop("AI_HATS_WT_GATE_OFF", None)

    proc = subprocess.run(
        [sys.executable, str(hook_script)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    data = json.loads(proc.stdout)
    hook_output = data.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "deny"
    assert "worktree-isolation" in hook_output.get("permissionDecisionReason", "")


@pytest.mark.integration
def test_agy_wt_gate_denies_create_and_target_file_keys(tmp_path: Path) -> None:
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-b", "master")
    _git(main, "config", "user.email", "t@t.io")
    _git(main, "config", "user.name", "t")
    (main / "main_code.py").write_text("print('hello')\n")
    _git(main, "add", ".")
    _git(main, "commit", "-m", "init")

    asm = Assembler(REPO_ROOT)
    result = asm.composer.compose("maintainer")
    provider = AgyProvider()
    provider.materialize_runtime_skills(main, result, "sid-agy-gate-create")
    provider.ensure_runtime_hooks(main, result)

    hook_script = main / ".agy" / "skills" / "worktree-isolation" / "hooks" / "wt_gate.py"

    # Test AGY tool 'Create' with TargetFile payload key
    payload = json.dumps({
        "hook_event_name": HOOK_PRE_TOOL_USE,
        "tool_name": "Create",
        "tool_input": {"TargetFile": str(main / "new_module.py")},
        "cwd": str(main),
    })

    env = os.environ.copy()
    env.pop("AI_HATS_WT_GATE_OFF", None)

    proc = subprocess.run(
        [sys.executable, str(hook_script)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )

    data = json.loads(proc.stdout)
    hook_output = data.get("hookSpecificOutput", {})
    assert hook_output.get("permissionDecision") == "deny"
    assert "worktree-isolation" in hook_output.get("permissionDecisionReason", "")

