"""e2e (HATS-1594)

flow:   an agent runs `ai-hats --role judge` in a project whose ai-hats.yaml
        still says `active_role: maintainer`, then commits from inside that
        session
cmds:
    ai-hats self init -p claude -r bare-role --no-wizard
    AI_HATS_SESSION_IDENTITY='{"v":1,...,"role":"gated-role",...}' git commit -m x
expect: the git gates that run are the ones the SESSION's role declares — the
        gated role's hook fires under the bare active_role, and the bare role's
        (none) fire under a gated active_role
why:    the dispatcher read `cfg.active_role or cfg.default_role`, so every
        commit inside a session launched with `--role` ran another role's gates.
        This also proves the envelope survives into `.githooks/<event>`, which
        git spawns with an environment ai-hats never touches — the transport
        assumption the whole task rests on
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.session_identity import ENV_SESSION_IDENTITY, IDENTITY_VERSION

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = [pytest.mark.integration, pytest.mark.guards]

GATE_HOOK = """#!/usr/bin/env bash
set -uo pipefail
touch "${AI_HATS_PROJECT_DIR:-$PWD}/.marker-gated-role"
exit 0
"""


def _binary_env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path, extra_env: dict[str, str] | None = None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(_binary_env())
    env.update(extra_env or {})
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env={k: v for k, v in env.items() if not k.startswith("GIT_")},
    )


def _git_ok(*args: str, cwd: Path) -> str:
    cp = _git(*args, cwd=cwd)
    assert cp.returncode == 0, f"git {' '.join(args)} failed:\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout.strip()


def _project(tmp_path: Path) -> Path:
    """A project with two roles: one shipping a pre-commit gate, one bare."""
    project = tmp_path / "project"
    project.mkdir()
    _git_ok("-c", "init.defaultBranch=master", "init", "--quiet", cwd=project)
    _git_ok("config", "user.email", "t@e.x", cwd=project)
    _git_ok("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "gate_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: gate_skill\n"
        "description: ships a pre-commit gate\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-commit:\n"
        "      - git_hooks/gate.sh\n"
        "---\n\n# Gate\n"
    )
    gate = skill / "git_hooks" / "gate.sh"
    gate.write_text(GATE_HOOK)
    gate.chmod(0o755)

    for name, skills in (("trait-gated", ["gate_skill"]), ("trait-bare", [])):
        trait = lib / "traits" / name
        trait.mkdir(parents=True)
        body = "".join(f"    - {s}\n" for s in skills) or "    []\n"
        (trait / "config.yaml").write_text(
            f"name: {name}\ncomposition:\n  skills:\n{body}injection: B.\n"
        )
    for role_name, trait_name in (("gated-role", "trait-gated"), ("bare-role", "trait-bare")):
        role = lib / "roles" / role_name
        role.mkdir(parents=True)
        (role / "config.yaml").write_text(
            f"name: {role_name}\npriorities: [Quality]\n"
            f"composition:\n  traits:\n    - {trait_name}\ninjection: R.\n"
        )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\nlibrary_paths:\n  - " + str(lib) + "\n"
    )
    return project


def _self_init(project: Path) -> None:
    """Always init under the gated role: the dispatcher stub is only installed
    when the composed role ships a git hook, and every case below needs it."""
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "claude", "-r", "gated-role", "--no-wizard"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "pre-commit").is_file(), "dispatcher not installed"


def _set_active_role(project: Path, role: str) -> None:
    """What a stale config looks like — `--role`/`-p` never write it back."""
    config = project / PROJECT_CONFIG
    lines = [
        line for line in config.read_text().splitlines() if not line.startswith("active_role:")
    ]
    config.write_text("\n".join([f"active_role: {role}", *lines]) + "\n")


def _envelope(project: Path, role: str) -> dict[str, str]:
    """The environment a session of ``role`` hands to everything it spawns."""
    return {
        ENV_SESSION_IDENTITY: json.dumps(
            {
                "v": IDENTITY_VERSION,
                "id": "20260812-000000-1-1",
                "role": role,
                "provider": "claude",
                "project_dir": str(project),
                "session_dir": str(project / ".agent" / "runs" / "session_x"),
                "skills_root": str(project / ".agent" / "mirror"),
            }
        ),
        "AI_HATS_SESSION_ID": "20260812-000000-1-1",
    }


def _commit(project: Path, name: str, extra_env: dict[str, str] | None = None):
    (project / name).write_text("x\n")
    _git_ok("add", name, cwd=project)
    return _git("commit", "-m", "test", "--quiet", cwd=project, extra_env=extra_env)


def test_the_session_role_decides_which_git_gates_run(tmp_path: Path):
    """HATS-1594 symptom 1: a judge session ran maintainer's gates, undeclared."""
    project = _project(tmp_path)
    _self_init(project)
    _set_active_role(project, "bare-role")
    marker = project / ".marker-gated-role"

    cp = _commit(project, "a.txt", _envelope(project, "gated-role"))

    assert cp.returncode == 0, f"commit failed:\n{cp.stdout}\n{cp.stderr}"
    assert marker.is_file(), (
        "the session composed gated-role, so its pre-commit gate must run — "
        f"active_role is bare-role and was followed instead.\n{cp.stdout}\n{cp.stderr}"
    )


def test_a_gate_the_session_did_not_compose_stays_silent(tmp_path: Path):
    """The other direction: active_role must not add a gate to a session
    that never declared it. Both halves are needed — following the envelope
    only when it happens to declare MORE gates would still be reading the config."""
    project = _project(tmp_path)
    _self_init(project)
    _set_active_role(project, "gated-role")
    marker = project / ".marker-gated-role"
    marker.unlink(missing_ok=True)

    cp = _commit(project, "b.txt", _envelope(project, "bare-role"))

    assert cp.returncode == 0, f"commit failed:\n{cp.stdout}\n{cp.stderr}"
    assert not marker.exists(), (
        "the session composed bare-role, which declares no gate; active_role's "
        f"gate ran anyway.\n{cp.stdout}\n{cp.stderr}"
    )


def test_outside_a_session_the_configured_role_still_decides(tmp_path: Path):
    """No envelope is not a degraded state — in a bare terminal the config IS
    the answer, and the fix must not disarm the gates an operator relies on."""
    project = _project(tmp_path)
    _self_init(project)
    _set_active_role(project, "gated-role")
    marker = project / ".marker-gated-role"
    marker.unlink(missing_ok=True)

    cp = _commit(project, "c.txt")

    assert cp.returncode == 0, f"commit failed:\n{cp.stdout}\n{cp.stderr}"
    assert marker.is_file(), f"no session, so active_role governs\n{cp.stdout}\n{cp.stderr}"
