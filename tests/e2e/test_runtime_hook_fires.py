"""e2e (HATS-601, HATS-607)

flow:   an agent executing tool calls that trigger skill-declared runtime hooks
cmds:
    ai-hats self init -p claude -r e2e-rthook-role --no-wizard
expect: runtime hook script executes for both PreToolUse and PostToolUse events writing
        side-effects
why:    without verifying hook body execution, dangling settings.json pointers fail
        silently without running logic
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import composed_row

from ai_hats.paths import strip_claude_project_dir
from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "runtime_hook_lib"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _init_with_fixture_role(launcher: Path, env: dict, project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _run(
        [str(launcher), "self", "init", "-p", "claude", "-r", "e2e-rthook-role", "--no-wizard"],
        cwd=project,
        env=env,
        timeout=120,
    )


@pytest.mark.integration
def test_e2e_runtime_hook_body_runs_for_both_events(installed_launcher, tmp_path):
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_fires"
    _init_with_fixture_role(launcher, env, project)

    from ai_hats.assembler import Assembler
    from ai_hats.paths import session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeSurface

    provider = ClaudeSurface()
    asm = Assembler(project)
    result = asm.composer.compose("e2e-rthook-role")
    provider.build_session_artifacts(
        project, result, "sid-rthook-fires", run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )
    cache_settings = session_cache_dir(project, "sid-rthook-fires") / "settings.json"
    pre_cmd = composed_row(cache_settings, "ai-hats:e2e-rthook:PreToolUse:Bash:probe")["command"]
    post_cmd = composed_row(cache_settings, "ai-hats:e2e-rthook:PostToolUse:Edit|Write:probe")["command"]
    # Both events route to the same materialized script (one declared script).
    assert pre_cmd == post_cmd
    # The command carries the $CLAUDE_PROJECT_DIR/ runtime placeholder (HATS-615);
    # strip it to get the on-disk, project-relative path.
    rel_path = strip_claude_project_dir(pre_cmd)
    script = project / rel_path
    assert script.is_file(), f"materialized script missing: {script}"

    marker = tmp_path / "marker.log"
    hook_env = {**env, "RTHOOK_MARKER": str(marker)}

    # Feed the script the exact payload shape Claude's hook channel sends,
    # once per event. The hook appends hook_event_name to the marker.
    for event, command in ((HOOK_PRE_TOOL_USE, pre_cmd), (HOOK_POST_TOOL_USE, post_cmd)):
        payload = json.dumps(
            {
                "hook_event_name": event,
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
        result = subprocess.run(
            ["bash", str(project / strip_claude_project_dir(command))],
            input=payload,
            cwd=str(project),
            env=hook_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"{event}: benign payload must exit 0; got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    # Side-effect proves both hook bodies executed end-to-end.
    assert marker.is_file(), "hook never wrote its marker — body did not run"
    recorded = marker.read_text().split()
    assert HOOK_PRE_TOOL_USE in recorded, f"PreToolUse hook body did not run: {recorded}"
    assert HOOK_POST_TOOL_USE in recorded, f"PostToolUse hook body did not run: {recorded}"
