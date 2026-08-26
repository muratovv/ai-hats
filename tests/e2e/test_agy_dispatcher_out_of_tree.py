"""e2e (HATS-1398)

flow:   an agent executing tool calls under an out-of-tree session cache location
cmds:
    # when AI_HATS_SESSION_CACHE_DIR points to an out-of-tree location
    ai-hats agent assistant --task "Execute edit"
expect: agy hook dispatcher resolves session hooks from out-of-tree cache and fires
        scripts
why:    without out-of-tree cache resolution, moving session cache out of workspace
        silently disables all registered runtime hooks
"""

from __future__ import annotations

from _helpers.sessions import stand_in_session

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import BuiltArtifacts, RunMode
from ai_hats.surfaces.agy.global_hook import DISPATCHER_COMMAND
from ai_hats.surfaces.agy.provider import AgySurface

pytestmark = pytest.mark.integration

SESSION_ID = "e2e-sid-dispatch"


@pytest.fixture
def agy_session(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    """A real agy session build; returns (project, session env, hook marker path)."""
    project = tmp_path / "project"
    project.mkdir()
    lib = tmp_path / "lib"
    marker = tmp_path / "hook_fired.txt"

    skill = lib / "skills" / "hook-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: hook-skill\ndescription: fires on Edit\n"
        "ai_hats:\n  runtime_hooks:\n    PreToolUse:\n"
        "      - matcher: Edit\n        script: fire.sh\n---\n# body\n"
    )
    (skill / "fire.sh").write_text(f"#!/bin/sh\necho FIRED > '{marker}'\n")
    (skill / "fire.sh").chmod(0o755)

    role = lib / "roles" / "hook-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: hook-role\npriorities:\n  - Quality\n"
        "composition:\n  skills: [hook-skill]\ninjection: Hook role body.\n"
    )

    ProjectConfig(provider="agy", library_paths=[str(lib)]).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[lib])
    asm.init()
    result = asm.composer.compose("hook-role")

    artifacts = BuiltArtifacts()
    provider = AgySurface()
    provider.build_session_artifacts(
        project, result, SESSION_ID, run_mode=RunMode.AUTOMATE, artifacts=artifacts
    )

    env = {
        **os.environ,
        **provider.get_env(project, project),
        **artifacts.extra_env,
        "AI_HATS_PYTHON": sys.executable,
    }
    # HATS-1594: a session is its envelope; the bare id reads as an older build.
    # After the spreads, so the out-of-tree cache pin this test is about survives.
    stand_in_session(env, project, SESSION_ID)
    return project, env, marker


def _dispatch(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run the production dispatcher command string through a real shell."""
    return subprocess.run(
        ["sh", "-c", DISPATCHER_COMMAND, "sh", "PreToolUse", "Edit"],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_dispatcher_fires_a_hook_from_the_out_of_tree_cache(agy_session) -> None:
    project, env, marker = agy_session

    pinned = Path(env["AI_HATS_SESSION_CACHE_DIR"])
    assert not pinned.is_relative_to(project), f"cache still inside the workspace: {pinned}"
    assert json.loads((pinned / "hooks.json").read_text())["PreToolUse"]

    proc = _dispatch(env, project)

    assert proc.returncode == 0, proc.stderr
    assert marker.is_file(), f"hook never fired; dispatcher stderr:\n{proc.stderr}"
    assert marker.read_text().strip() == "FIRED"


def test_dispatcher_without_the_pin_says_so(agy_session) -> None:
    """A pre-move session must degrade loudly, not look like 'no hooks here'."""
    project, env, _marker = agy_session
    env.pop("AI_HATS_SESSION_CACHE_DIR")

    proc = _dispatch(env, project)

    assert proc.returncode == 0
    assert "AI_HATS_SESSION_CACHE_DIR unset" in proc.stderr
