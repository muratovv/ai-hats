"""e2e (HATS-601)

flow:   a developer initializing a project with a role that declares skill runtime hooks
cmds:
    ai-hats self init -p claude -r e2e-rthook-role --no-wizard
expect: runtime hooks are wired into settings.json and materialized executable scripts
        return correct codes
why:    without end-to-end hook propagation, skill runtime hooks are dropped during
        session initialization
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import HOOK_POST_TOOL_USE, HOOK_PRE_TOOL_USE


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "runtime_hook_lib"
SESSION_ID = "sid-rthook-prop"
SKILL = "e2e-rthook"
# The source relpath — the mirror keeps it; only the retired flatten renamed
# it to <skill>-<basename>.
SCRIPT_RELPATH = "hooks/probe.sh"


def _expected_command(project: Path) -> str:
    """Where the session mirror puts the declared script (HATS-1268).

    Derived from the same helpers the impl uses, so this test cannot silently
    drift from it — the drift that caused HATS-645.
    """
    from ai_hats.paths import claude_plugin_skills_dir, session_cache_dir

    skills = claude_plugin_skills_dir(session_cache_dir(project, SESSION_ID) / "plugin")
    return str(skills / SKILL / SCRIPT_RELPATH)


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
    """Copy the fixture library into ``<project>/libraries`` and init the role.

    ``<project>/libraries`` is auto-appended to the resolver's library paths
    (Assembler._build_library_paths), so ``-r e2e-rthook-role`` resolves the
    fixture role + its ``e2e-rthook`` skill without touching ai-hats.yaml.
    """
    project.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE_LIB, project / "libraries")
    _run(
        [str(launcher), "self", "init", "-p", "claude", "-r", "e2e-rthook-role", "--no-wizard"],
        cwd=project,
        env=env,
        timeout=120,
    )


def _managed_entries(settings: dict) -> dict[str, list[dict]]:
    hooks = settings.get("hooks", {})
    return {event: entries for event, entries in hooks.items() if isinstance(entries, list)}


# ---------------------- A + B: wiring + materialization ----------------------


@pytest.mark.integration
def test_e2e_skill_runtime_hook_wired_and_materialized(installed_launcher, tmp_path):
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_wire"
    _init_with_fixture_role(launcher, env, project)

    from ai_hats.assembler import Assembler
    from ai_hats.paths import session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeSurface

    provider = ClaudeSurface()
    asm = Assembler(project)
    result = asm.composer.compose("e2e-rthook-role")
    provider.build_session_artifacts(
        project, result, SESSION_ID, run_mode=RunMode.HITL, artifacts=BuiltArtifacts()
    )
    cache_settings = session_cache_dir(project, SESSION_ID) / "settings.json"
    settings = json.loads(cache_settings.read_text())
    by_event = _managed_entries(settings)

    # A. PreToolUse managed entry for the skill, tagged with the matcher.
    pre = by_event.get(HOOK_PRE_TOOL_USE, [])
    sp = [e for e in pre if e.get("_ai_hats_managed") == "ai-hats:e2e-rthook:PreToolUse:Bash"]
    assert len(sp) == 1, f"missing PreToolUse skill entry in {pre}"
    assert sp[0]["matcher"] == "Bash"
    assert sp[0]["hooks"] == [{"type": "command", "command": _expected_command(project)}]
    # No guard entry here, and that is the contract: this fixture role composes
    # `e2e-rthook` alone, and since HATS-1268 every entry is skill-declared —
    # nothing is wired unconditionally any more.
    assert not [
        e for e in pre if str(e.get("_ai_hats_managed", "")).startswith("ai-hats:safety-guard")
    ]

    # A. PostToolUse managed entry under its own event.
    post = by_event.get(HOOK_POST_TOOL_USE, [])
    pe = [
        e for e in post if e.get("_ai_hats_managed") == "ai-hats:e2e-rthook:PostToolUse:Edit|Write"
    ]
    assert len(pe) == 1, f"missing PostToolUse skill entry in {post}"
    assert pe[0]["matcher"] == "Edit|Write"
    assert pe[0]["hooks"] == [{"type": "command", "command": _expected_command(project)}]

    # B. The script settings.json points at exists and is executable — the
    # command is the on-disk path now, absolute into the session mirror.
    materialized = Path(_expected_command(project))
    assert materialized.is_file(), f"materialized script missing: {materialized}"
    assert stat.S_IMODE(materialized.stat().st_mode) == 0o755


# ---------------------- C: live propagation ----------------------


@pytest.mark.integration
def test_e2e_materialized_runtime_hook_is_live(installed_launcher, tmp_path):
    """Pipe Claude's tool_input JSON shape into the materialized script →
    contracted exit code. Proves the chain is live (vs a dangling pointer)."""
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_rthook_live"
    _init_with_fixture_role(launcher, env, project)

    from ai_hats.assembler import Assembler
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeSurface

    ClaudeSurface().build_session_artifacts(
        project,
        Assembler(project).composer.compose("e2e-rthook-role"),
        SESSION_ID,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    script = Path(_expected_command(project))
    assert script.is_file(), "precondition: the session mirror must have been built"

    deny = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"tool_input": {"command": "RTHOOK_DENY"}}),
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert deny.returncode == 2, (
        f"sentinel payload must exit 2; got {deny.returncode}\n"
        f"stdout:\n{deny.stdout}\nstderr:\n{deny.stderr}"
    )

    allow = subprocess.run(
        ["bash", str(script)],
        input=json.dumps({"tool_input": {"command": "ls -la"}}),
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert allow.returncode == 0, (
        f"benign payload must exit 0; got {allow.returncode}\n"
        f"stdout:\n{allow.stdout}\nstderr:\n{allow.stderr}"
    )
