"""e2e (HATS-1102)

flow:   an agent executing write tools targeting code files in main checkout under agy
cmds:
    # when attempting to edit main checkout files in agy session
    ai-hats execute -p agy --batch -r maintainer --prompt "Edit main"
expect: worktree gate hook denies destructive writes in main checkout
why:    without worktree gate hooks materialized for agy, agents make unauthorized
        direct edits to main checkout
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout
from _helpers.git import git as _git

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.constants import HOOK_PRE_TOOL_USE
from ai_hats.surfaces.agy.provider import AgySurface

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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
    provider = AgySurface()
    provider.build_session_prompt(ProjectLayout.at(main), result, "sid-agy-gate")

    hooks_file = ProjectLayout.at(main).cache.session("sid-agy-gate") / "hooks.json"
    assert hooks_file.is_file(), "hooks.json must be created in session cache"
    hooks_data = json.loads(hooks_file.read_text())
    pre_tool_hooks = hooks_data.get("PreToolUse", [])
    # The manifest keeps the row's own (Claude) matcher; knowing that agy calls
    # the same tool `Create` is the dispatcher's job, via `claude_hook_adapter`
    # (HATS-1776) — pinned by test_claude_hook_adapter.py, which asks the
    # translator directly instead of reading a rewritten string out of a file.
    assert any(
        "wt_gate.py" in h.get("command", "") and "Edit" in h.get("matcher", "")
        for h in pre_tool_hooks
        if isinstance(h, dict)
    ), "wt_gate.py PreToolUse row must be in agy hooks.json"

    from ai_hats.surfaces.agy.claude_hook_adapter import matches_claude_hook

    matcher = next(h["matcher"] for h in pre_tool_hooks if "wt_gate.py" in h.get("command", ""))
    assert matches_claude_hook(matcher, "Create"), "the row must answer agy's own tool name"

    hook_script = (
        ProjectLayout.at(main).cache.session("sid-agy-gate")
        / "rules"
        / ".agents"
        / "skills"
        / "worktree-isolation"
        / "hooks"
        / "wt_gate.py"
    )
    assert hook_script.is_file(), "wt_gate.py must be materialized in session cache dir"

    # Test payload targeting code file in MAIN checkout
    payload = json.dumps(
        {
            "hook_event_name": HOOK_PRE_TOOL_USE,
            "tool_name": "Write",
            "tool_input": {"file_path": str(main / "main_code.py")},
            "cwd": str(main),
        }
    )

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
    provider = AgySurface()
    provider.materialize_runtime_skills(ProjectLayout.at(main), result, "sid-agy-gate-create")
    provider.ensure_runtime_hooks(ProjectLayout.at(main), result, session_id="sid-agy-gate-create")

    hook_script = (
        ProjectLayout.at(main).cache.session("sid-agy-gate-create")
        / "rules"
        / ".agents"
        / "skills"
        / "worktree-isolation"
        / "hooks"
        / "wt_gate.py"
    )

    # AGY's own tool and argument spellings, translated the way the surface
    # translates them at spawn (`claude_hook_adapter`, HATS-1776). The script's
    # private five-key fan-out is gone: one dialect reaches it now, and what
    # this test still proves is the CHAIN — agy's spelling reaches a deny.
    from ai_hats.surfaces.agy.claude_hook_adapter import to_claude_payload

    payload = json.dumps(
        to_claude_payload(
            {
                "hook_event_name": HOOK_PRE_TOOL_USE,
                "toolCall": {
                    "name": "Create",
                    "args": {"TargetFile": str(main / "new_module.py")},
                },
                "cwd": str(main),
            }
        )
    )

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
