"""HATS-1337 e2e — the orchestrator's three promises, on real commits.

Real ``self init`` + real ``git commit``/``worktree`` (``dev_rule_e2e_gate``):

* **fail-open** — ai-hats gone, a human commit still lands (R2);
* **worktree parity** — a commit inside a linked worktree runs the SAME gates as
  the main checkout (R1, the Z1 regression);
* **survives update** — the next commit after a version flip runs the NEW
  version's gates with no re-materialization step in between (R9 / M12).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = pytest.mark.integration

GUARD = """#!/usr/bin/env bash
set -uo pipefail
echo "GUARD ${AI_HATS_HOOK_EVENT} v${GUARD_VERSION:-1}" >&2
touch "$(git rev-parse --show-toplevel)/.marker-guard"
exit 0
"""


def _pinned_env() -> dict[str, str]:
    """Env pinning ai-hats to THIS checkout, GIT_* stripped (HATS-887)."""
    from _helpers.env import checkout_pythonpath

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path, env: dict[str, str] | None = None):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        env=env or _pinned_env(),
    )


def _git_ok(*args: str, cwd: Path, env: dict[str, str] | None = None) -> str:
    cp = _git(*args, cwd=cwd, env=env)
    assert cp.returncode == 0, f"git {' '.join(args)}:\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout.strip()


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    _git_ok("-c", "init.defaultBranch=master", "init", "--quiet", cwd=project)
    _git_ok("config", "user.email", "t@e.x", cwd=project)
    _git_ok("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "guard_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: guard_skill\ndescription: a pre-commit guard\n"
        "ai_hats:\n  git_hooks:\n    pre-commit:\n      - git_hooks/guard.sh\n---\n\n# G\n"
    )
    guard = skill / "git_hooks" / "guard.sh"
    guard.write_text(GUARD)
    guard.chmod(0o755)

    (lib / "traits" / "trait-base").mkdir(parents=True)
    (lib / "traits" / "trait-base" / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - guard_skill\ninjection: B.\n"
    )
    (lib / "roles" / "guard-role").mkdir(parents=True)
    (lib / "roles" / "guard-role" / "config.yaml").write_text(
        "name: guard-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(f"provider: claude\nlibrary_paths:\n  - {lib}\n")
    return project, lib


def _self_init(project: Path) -> None:
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "claude", "-r", "guard-role", "--no-wizard"],
        cwd=str(project),
        env=_pinned_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "pre-commit").is_file(), "dispatcher not installed"


def _commit(project: Path, name: str, *, env: dict[str, str] | None = None, cwd: Path | None = None):
    where = cwd or project
    (where / name).write_text("x\n")
    _git_ok("add", name, cwd=where, env=env)
    return _git("commit", "-m", name, "--quiet", cwd=where, env=env)


def test_a_commit_lands_when_ai_hats_is_gone(tmp_path: Path):
    """R2: fail-open. The old dispatcher refused the event and prescribed
    `ai-hats self init` — the very binary that is missing."""
    project, _lib = _project(tmp_path)
    _self_init(project)

    # ai-hats fully unavailable to the hook: no pinned venv, no importable package.
    stripped = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "AI_HATS_"))}
    stripped["PYTHONPATH"] = str(tmp_path / "nothing")
    stripped["PATH"] = "/usr/bin:/bin"

    cp = _commit(project, "a.txt", env=stripped)

    assert cp.returncode == 0, f"a human commit must never be wedged:\n{cp.stderr}"
    assert "fail-open" in cp.stderr, cp.stderr
    assert not (project / ".marker-guard").exists(), "the gate cannot have run"


def test_a_worktree_commit_runs_the_same_gates(tmp_path: Path):
    """R1 / Z1: `.githooks/` is generated and gitignored, so a relative
    `core.hooksPath` resolved to nothing inside a linked worktree and every gate
    was silently off there. Absolute closes it."""
    project, _lib = _project(tmp_path)
    _self_init(project)
    _commit(project, "seed.txt")

    worktree = tmp_path / "wt"
    _git_ok("worktree", "add", "-q", "-b", "task/x", str(worktree), cwd=project)
    assert not (worktree / ".githooks").exists(), "the fixture must not fake a copy"

    cp = _commit(project, "inwt.txt", cwd=worktree)

    assert cp.returncode == 0, f"{cp.stdout}\n{cp.stderr}"
    assert "GUARD pre-commit" in cp.stderr, (
        f"no gate ran inside the worktree — Z1 regression\n{cp.stderr}"
    )
    assert (worktree / ".marker-guard").exists()


def test_the_next_commit_after_an_update_runs_the_new_gates(tmp_path: Path):
    """R9 / M12: the dispatcher is not bound to a version, so a `self update`
    needs no re-materialization step before the gates change."""
    project, lib = _project(tmp_path)
    _self_init(project)
    _commit(project, "before.txt")
    assert "GUARD pre-commit v1" in _commit(project, "v1.txt").stderr

    # The library ships a new gate version. Nothing re-runs `self init`.
    guard = lib / "skills" / "guard_skill" / "git_hooks" / "guard.sh"
    guard.write_text(GUARD.replace("v${GUARD_VERSION:-1}", "v2"))
    guard.chmod(0o755)
    dispatcher_before = (project / ".githooks" / "pre-commit").read_bytes()

    cp = _commit(project, "after.txt")

    assert cp.returncode == 0, cp.stderr
    assert "GUARD pre-commit v2" in cp.stderr, (
        f"the commit ran the OLD gate — the orchestrator is version-bound\n{cp.stderr}"
    )
    assert (project / ".githooks" / "pre-commit").read_bytes() == dispatcher_before, (
        "the durable artifact must survive by content, not be re-materialized"
    )
