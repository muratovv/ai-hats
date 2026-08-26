"""e2e (HATS-437, HATS-467)

flow:   an agent executing destructive tool commands in an initialized project
cmds:
    # in an initialized project workspace
    gh pr merge 42 --merge --delete-branch
expect: hook scripts are written to disk with executable permissions and block
        unacknowledged destructive tool commands
why:    PreToolUse guards rely on materialized script files on disk to enforce state
        safety rules during agent execution
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"
# HATS-1268: the guard is skill-declared now, so it reaches library/hooks/ as a
# flattened skill copy. Its classifier sibling is NOT declared, so it is not
# flattened alongside — that copy is write-only residue awaiting HATS-1480, and
# the test that actually RUNS the guard uses the session mirror instead.
GUARD_FLAT_NAME = "safety-guard-pre_bash_shared_state_guard.sh"
HOOK_BASENAMES = (GUARD_FLAT_NAME,)

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.env import clean_env  # noqa: E402
from _helpers.project import pin_edge_channel  # noqa: E402
from _helpers.repo_src import build_src  # noqa: E402
from ai_hats.paths import ENV_AI_HATS_VENV  # noqa: E402
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL  # noqa: E402


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


@pytest.fixture(scope="module")
def private_launcher(tmp_path_factory):
    """Private module-scoped builder for the LONE venv-mutating test (HATS-582).

    Test C runs ``self update --force-downgrade``, which reinstalls ai-hats
    into the pinned venv — a destructive mutation that would poison the
    session-shared venv. So this test keeps its own private build (the old
    module fixture, unchanged).
    """
    tmp = tmp_path_factory.mktemp("launcher")
    launcher_dest = tmp / "bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    # HATS-904: module setup runs before the autouse env scrub — scrub explicitly.
    # ai-hats init flow: README.md#2-wire-ai-hats-into-a-project
    env = clean_env()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env.pop(ENV_AI_HATS_VENV, None)
    # Isolate from the user's ``~/.ai-hats/`` customizations layer
    # (roles, traits, customizations.yaml) — otherwise the dev env's
    # personal-workflow trait and custom roles bleed into the test
    # and shadow framework roles. HOME redirected to an empty tmpdir.
    isolated_home = tmp / "home"
    isolated_home.mkdir()
    env["HOME"] = str(isolated_home)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp, env=env, timeout=30)
    bootstrap_proj = tmp / "_bootstrap_proj"
    bootstrap_proj.mkdir()
    # --force-downgrade: required when this dev env is ahead of
    # origin/master (HATS-441 guard would otherwise refuse the install
    # of the local repo path). Safe in test: we own the bootstrap_proj
    # entirely. Same workaround used by long-running e2e fixtures.
    # HATS-673: timeout 300 (not 180) — this is a REAL pip install of
    # ai-hats into a fresh venv (~118s solo). Under the master gate's
    # `-n8 --dist=loadgroup` run, concurrent pip installs across workers
    # push a single build past 180s and FLAKE the gate. 300s is the
    # suite-proven ceiling: ~10 sibling test_self_update_* tests do the
    # same real-pip `self update` under the same -n8 contention at 300s
    # and don't flake (crash_safety / orphan_gc / versioned). This call
    # was the lone <300 outlier on the pip path.
    _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=bootstrap_proj,
        env=env,
        timeout=300,
    )
    shared_venv = bootstrap_proj / ".agent" / "ai-hats" / ".venv"
    assert shared_venv.is_dir(), "bootstrap did not create shared venv"
    env[ENV_AI_HATS_VENV] = str(shared_venv)
    return launcher_dest, env, shared_venv


def _init_minimal_project(launcher: Path, env: dict, project: Path) -> None:
    """Wire ai-hats into ``project`` with assistant role + Claude provider."""
    project.mkdir(exist_ok=True)
    _run(
        [str(launcher), "self", "init", "-p", "claude", "-r", "assistant", "--no-wizard"],
        cwd=project,
        env=env,
        timeout=120,
    )
    pin_edge_channel(project)  # HATS-764: edge for the subsequent self update


def _materialized_hooks_dir(project: Path) -> Path:
    return project / ".agent" / "ai-hats" / "library" / "hooks"


def _package_hook_source_bytes(name: str) -> bytes:
    """Read the source hook body straight from the ``ai_hats_library`` package.

    Reads the library tree rather than the test venv's import machinery. Since
    HATS-1268 the guard ships inside the skill that declares it, so the flat
    materialized name maps back to ``<skill>/hooks/<basename>``.
    """
    lib = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
    if name == GUARD_FLAT_NAME:
        return (lib / "core/skills/safety-guard/hooks/pre_bash_shared_state_guard.sh").read_bytes()
    return (lib / "hooks" / name).read_bytes()


# ---------------------- Test D: safety net live ----------------------


# ---------------------- Test D: safety net live ----------------------


@pytest.mark.integration
def test_e2e_materialized_hook_blocks_irreversible_no_tty(installed_launcher, tmp_path):
    """Materialized hook returns exit 2 for irreversible commands w/o TTY.

    This is the proof-of-life contract for HATS-437: without
    materialization, settings.json points at a missing file and the
    safety net is silently dead. After HATS-467 lands, the hook is
    executable AND functional — given a tool_input JSON matching the
    classifier's irreversible patterns, it exits 2 (deny).

    Fail-under-revert: drop _materialize_pretooluse_hooks() and the
    script does not exist; the bash invocation here would fail with
    "No such file or directory" (exit 127) instead of the contract's
    exit 2.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_safety_net_live"
    _init_minimal_project(launcher, env, project)

    # HATS-1268: run the copy that has its siblings. The flattened copy under
    # library/hooks/ no longer carries shared_state_classifier.sh, so executing
    # THAT one would test a corpse — the session mirror is where the guard lives.
    from ai_hats.assembler import Assembler
    from ai_hats.paths import claude_plugin_skills_dir, session_cache_dir
    from ai_hats.session_artifacts import BuiltArtifacts, RunMode
    from ai_hats.surfaces.claude.provider import ClaudeSurface

    sid = "sid-guard-live"
    ClaudeSurface().build_session_artifacts(
        project,
        Assembler(project).composer.compose("assistant"),
        sid,
        run_mode=RunMode.HITL,
        artifacts=BuiltArtifacts(),
    )
    skills = claude_plugin_skills_dir(session_cache_dir(project, sid) / "plugin")
    guard = skills / "safety-guard" / "hooks" / "pre_bash_shared_state_guard.sh"
    assert guard.is_file(), "precondition: the session mirror must have been built"
    assert (guard.parent / "shared_state_classifier.sh").is_file(), (
        "precondition: the classifier must be beside the guard"
    )

    # Tool-input JSON the classifier recognises as irreversible.
    # The classifier sees the literal command and matches the
    # PR-merge pattern → "irreversible" → no TTY → exit 2.
    payload = json.dumps(
        {
            "tool_input": {
                "command": "gh pr merge 42 --merge --delete-branch",
            },
        }
    )

    env_no_ack = {k: v for k, v in env.items() if k != "AI_HATS_SHARED_STATE_ACK"}
    result = subprocess.run(
        ["bash", str(guard)],
        input=payload,
        cwd=str(project),
        env=env_no_ack,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, (
        f"hook must deny irreversible command in non-TTY context with "
        f"exit 2; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "shared-state-guard" in (result.stdout + result.stderr).lower(), (
        f"hook stderr should mention 'shared-state-guard'; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
