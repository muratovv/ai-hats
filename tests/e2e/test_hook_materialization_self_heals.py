"""e2e (HATS-593, HATS-833)

flow:   a developer executing git push when managed hook scripts are missing or
        corrupted
cmds:
    # in a project with a missing or corrupted pre-push hook script
    git push origin master
expect: missing hook scripts fail open on execution without blocking git commands and
        session start restores missing script files
why:    corrupted or removed hook scripts must not block developer git workflow while
        ensuring automated recovery on session launch
"""

from __future__ import annotations
from _helpers.git import git as _git_helper

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790: invoke the dev-venv ai-hats as `python -m ai_hats` — the
# bin/ai-hats console-script generator was removed.
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = [pytest.mark.integration, pytest.mark.guards]


def _binary_env() -> dict[str, str]:
    """Env pinning the ``ai-hats`` binary to THIS worktree's code (PYTHONPATH=src
    so the editable install doesn't resolve the main checkout)."""
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


# ----- Guarantee 1: `self sync-hooks` removed -------------------------------


def test_self_sync_hooks_command_removed(tmp_path: Path):
    """The standalone ``ai-hats self sync-hooks`` command no longer exists
    (HATS-833 consolidated healing to session start)."""
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "sync-hooks"],
        cwd=str(tmp_path),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert cp.returncode != 0, (
        f"`self sync-hooks` should be GONE but exited 0:\n{cp.stdout}\n{cp.stderr}"
    )
    combined = (cp.stdout + cp.stderr).lower()
    assert "no such command" in combined or "usage" in combined, (
        f"expected a click 'no such command' error, got:\n{cp.stdout}\n{cp.stderr}"
    )


# ----- Guarantee 3: fail-closed dispatcher backstop (unchanged) -------------


def _make_gate_project(tmp_path: Path) -> tuple[Path, Path]:
    """Real git project + synthetic library whose role ships a pre-push gate
    (the protected hook). No self-heal hooks — HATS-833 removed that surface."""
    project = tmp_path / "project"
    project.mkdir()
    _git("init", "--quiet", cwd=project)
    _git("config", "user.email", "test@example.com", cwd=project)
    _git("config", "user.name", "test", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "gate_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: gate_skill\n"
        "description: ships a pre-push gate\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-push:\n"
        "      - git_hooks/gate.sh\n"
        "---\n\n# Gate\n"
    )
    gate = skill / "git_hooks" / "gate.sh"
    gate.write_text("#!/usr/bin/env bash\necho 'GATE v1'\nexit 0\n")
    gate.chmod(0o755)

    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - gate_skill\ninjection: B.\n"
    )
    role = lib / "roles" / "gate-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: gate-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\nlibrary_paths:\n  - " + str(lib) + "\n"
    )
    return project, lib


def _self_init(project: Path) -> None:
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "claude", "-r", "gate-role", "--no-wizard"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"


@pytest.fixture
def initialised_project(tmp_path: Path):
    project, lib = _make_gate_project(tmp_path)
    _self_init(project)
    githooks = project / ".githooks"
    assert (githooks / "pre-push").is_file(), "pre-push dispatcher not installed"
    # HATS-1337: the dispatcher is the ONLY thing installed; the gate stays in
    # the library and is resolved at spawn time.
    assert not (githooks / "pre-push.d").exists()
    return project, lib


def test_a_missing_gate_no_longer_blocks_the_push(initialised_project):
    """HATS-1337 inverts HATS-593: degradation is fail-OPEN.

    The old dispatcher refused the event when a managed hook went missing and
    told the operator to run `ai-hats self init` — the one command that needs
    exactly the binary whose absence caused the degradation. A gate that cannot
    be resolved is a gate that does not run, never a push that cannot happen.
    """
    project, _lib = initialised_project
    githooks = project / ".githooks"

    cp = subprocess.run(
        [str(githooks / "pre-push")],
        cwd=str(project),
        input="refs/heads/master " + "1" * 40 + " refs/heads/master " + "2" * 40 + "\n",
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert cp.returncode == 0, (
        f"a degraded install must not wedge the push\nstdout:{cp.stdout}\nstderr:{cp.stderr}"
    )
    assert "corrupt" not in cp.stderr
    assert "ai-hats self init" not in cp.stderr


def test_the_resolved_gate_runs_through_the_installed_dispatcher(initialised_project):
    """Chain-level (HATS-1113): compose -> install -> dispatcher -> library gate."""
    project, _lib = initialised_project
    githooks = project / ".githooks"

    cp = subprocess.run(
        [str(githooks / "pre-push")],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert cp.returncode == 0, f"intact gate must not block:\n{cp.stderr}"
    assert "GATE v1" in cp.stdout, (
        f"the gate never ran through the dispatcher\nstdout:{cp.stdout}\nstderr:{cp.stderr}"
    )


def _git(*args: str, cwd: Path) -> None:
    _git_helper(cwd, *args)
