"""HATS-999 — e2e: ai-hats git hooks coexist with a repo's own hook manager.

Real ``git commit``/``push`` through the live dispatcher + real ``self init``
(``dev_rule_e2e_gate``). Managers are fabricated as the file artifacts they
produce (simple-git-hooks → executable ``.git/hooks/<event>``; husky → own
``core.hooksPath`` dir) — no node needed, same contract git sees.
Fail-under-revert: without chaining the project marker never appears
(dispatcher shadows ``.git/hooks``); without auto-takeover the guard marker
never appears (``core.hooksPath`` stays ``.husky``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# HATS-790: no bin/ai-hats console script — invoke as `python -m ai_hats`.
AI_HATS_PYTHON = Path(sys.executable)
AI_HATS_CMD = (str(AI_HATS_PYTHON), "-m", "ai_hats")

pytestmark = pytest.mark.integration

# The ai-hats guard analog: a skill-declared hook that records it fired.
# ``AI_HATS_HOOK_EVENT`` is exported by the dispatcher (HATS-593).
GUARD_HOOK = """#!/usr/bin/env bash
set -uo pipefail
touch ".marker-guard-${AI_HATS_HOOK_EVENT}"
exit 0
"""


def _binary_env() -> dict[str, str]:
    """Env pinning the ``ai-hats`` binary to THIS worktree's code."""
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    return env


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # HATS-887: strip GIT_* so ambient GIT_DIR can't retarget the repo.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


def _git_ok(*args: str, cwd: Path) -> str:
    cp = _git(*args, cwd=cwd)
    assert cp.returncode == 0, f"git {' '.join(args)} failed:\n{cp.stdout}\n{cp.stderr}"
    return cp.stdout.strip()


def _write_hook(path: Path, marker_name: str, *, shell: str = "sh") -> None:
    """A project-manager-shaped hook: plain ``sh`` (card constraint), one marker."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/{shell}\ntouch {marker_name}\nexit 0\n")
    path.chmod(0o755)


def _make_coexist_project(tmp_path: Path) -> Path:
    """Real git project + synthetic library whose role ships pre-commit and
    pre-push guard hooks (the ai-hats-guard analogs)."""
    project = tmp_path / "project"
    project.mkdir()
    _git_ok("-c", "init.defaultBranch=master", "init", "--quiet", cwd=project)
    _git_ok("config", "user.email", "t@e.x", cwd=project)
    _git_ok("config", "user.name", "t", cwd=project)

    lib = tmp_path / "lib"
    skill = lib / "skills" / "guard_skill"
    (skill / "git_hooks").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: guard_skill\n"
        "description: ships pre-commit and pre-push guards\n"
        "ai_hats:\n"
        "  git_hooks:\n"
        "    pre-commit:\n"
        "      - git_hooks/guard.sh\n"
        "    pre-push:\n"
        "      - git_hooks/guard.sh\n"
        "---\n\n# Guard\n"
    )
    guard = skill / "git_hooks" / "guard.sh"
    guard.write_text(GUARD_HOOK)
    guard.chmod(0o755)

    trait = lib / "traits" / "trait-base"
    trait.mkdir(parents=True)
    (trait / "config.yaml").write_text(
        "name: trait-base\ncomposition:\n  skills:\n    - guard_skill\ninjection: B.\n"
    )
    role = lib / "roles" / "guard-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: guard-role\npriorities: [Quality]\n"
        "composition:\n  traits:\n    - trait-base\ninjection: R.\n"
    )
    (project / PROJECT_CONFIG).write_text(
        "provider: claude\nlibrary_paths:\n  - " + str(lib) + "\n"
    )
    return project


def _self_init(project: Path) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(
        [*AI_HATS_CMD, "self", "init", "-p", "claude", "-r", "guard-role", "--no-wizard"],
        cwd=str(project),
        env=_binary_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "pre-commit").is_file(), "dispatcher not installed"
    return cp


def _commit_file(project: Path, name: str = "f.txt") -> subprocess.CompletedProcess[str]:
    (project / name).write_text("x\n")
    _git_ok("add", name, cwd=project)
    return _git("commit", "-m", "test", "--quiet", cwd=project)


# ----- simple-git-hooks shape: project hook lives at .git/hooks/<event> -----


def test_simple_git_hooks_coexistence_on_commit(tmp_path: Path):
    """After ``self init`` on a repo whose manager wrote ``.git/hooks/pre-commit``,
    a real commit must fire BOTH the ai-hats guard AND the project hook."""
    project = _make_coexist_project(tmp_path)
    # simple-git-hooks shape: `prepare` wrote a plain executable hook, hooksPath unset.
    _write_hook(project / ".git" / "hooks" / "pre-commit", ".marker-project-pre-commit")

    _self_init(project)
    assert _git_ok("config", "--get", "core.hooksPath", cwd=project) == ".githooks"

    cp = _commit_file(project)
    assert cp.returncode == 0, f"commit failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".marker-guard-pre-commit").exists(), "ai-hats guard did not fire on commit"
    assert (project / ".marker-project-pre-commit").exists(), (
        "project hook (.git/hooks/pre-commit) did not fire — the dispatcher "
        "shadowed the repo's own hook manager (HATS-999 regression)"
    )


# ----- husky shape: core.hooksPath pre-set to the manager's own dir ---------


def test_preset_hookspath_taken_over_with_chaining(tmp_path: Path):
    """Pre-set ``core.hooksPath`` (husky shape): ``self init`` must take over
    LOUDLY, record the previous dir, and chain — both stacks fire on commit."""
    project = _make_coexist_project(tmp_path)
    _write_hook(project / ".husky" / "pre-commit", ".marker-husky-pre-commit")
    _git_ok("config", "core.hooksPath", ".husky", cwd=project)

    cp = _self_init(project)
    assert _git_ok("config", "--get", "core.hooksPath", cwd=project) == ".githooks", (
        "self init left core.hooksPath at '.husky' — ai-hats guards silently absent"
    )
    assert _git_ok("config", "--get", "ai-hats.previousHooksPath", cwd=project) == ".husky", (
        "previous hooks dir not recorded for chaining"
    )
    combined = cp.stdout + cp.stderr
    assert ".husky" in combined and "core.hooksPath" in combined, (
        f"takeover must be announced loudly, got:\n{combined}"
    )
    assert "git config core.hooksPath .husky" in combined, (
        f"takeover notice must carry the revert command, got:\n{combined}"
    )

    commit = _commit_file(project)
    assert commit.returncode == 0, f"commit failed:\n{commit.stdout}\n{commit.stderr}"
    assert (project / ".marker-guard-pre-commit").exists(), (
        "ai-hats guard did not fire after takeover"
    )
    assert (project / ".marker-husky-pre-commit").exists(), (
        "project hook (.husky/pre-commit) did not fire — takeover dropped the "
        "repo's own hook manager (HATS-999 regression)"
    )


# ----- chain semantics: stdin protocol, exit code, foreign .d/ survival -----


def test_prepush_chain_receives_stdin_protocol(tmp_path: Path):
    """The chained project pre-push hook must see git's ref protocol (HATS-654
    replay extends to the chain), not the EOF left after the guards ran."""
    project = _make_coexist_project(tmp_path)
    hook = project / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\ncat > .marker-project-pre-push\nexit 0\n")
    hook.chmod(0o755)

    _self_init(project)
    assert _commit_file(project).returncode == 0

    bare = tmp_path / "remote.git"
    _git_ok("-c", "init.defaultBranch=master", "init", "--bare", "--quiet", str(bare), cwd=tmp_path)
    _git_ok("remote", "add", "origin", str(bare), cwd=project)
    push = _git("push", "origin", "master", cwd=project)

    assert push.returncode == 0, f"push failed:\n{push.stdout}\n{push.stderr}"
    assert (project / ".marker-guard-pre-push").exists(), "guard did not fire on push"
    protocol = (project / ".marker-project-pre-push").read_text()
    head = _git_ok("rev-parse", "HEAD", cwd=project)
    assert protocol.strip() == f"refs/heads/master {head} refs/heads/master {'0' * 40}", (
        f"chained hook saw a wrong/empty ref protocol: {protocol!r}"
    )


def test_failing_project_hook_blocks_commit(tmp_path: Path):
    """A non-zero chained project hook must block the event, like any native hook."""
    project = _make_coexist_project(tmp_path)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho 'project hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)

    _self_init(project)
    cp = _commit_file(project)

    assert cp.returncode != 0, "commit passed despite a failing project hook"
    assert "chained project hook" in cp.stderr, f"no chain-failure notice:\n{cp.stderr}"


def test_foreign_dot_d_entry_survives_reinit(tmp_path: Path):
    """A user-added ``.d/`` script (the HATS-999 manual-workaround shape) must
    survive a repeated ``self init`` — sweep is manifest-scoped."""
    project = _make_coexist_project(tmp_path)
    _self_init(project)
    foreign = project / ".githooks" / "pre-commit.d" / "zz-user-custom.sh"
    foreign.write_text("#!/bin/sh\ntouch .marker-foreign-entry\nexit 0\n")
    foreign.chmod(0o755)

    _self_init(project)

    assert foreign.is_file(), "re-init swept a foreign .d/ entry"
    assert _commit_file(project).returncode == 0
    assert (project / ".marker-foreign-entry").exists(), "surviving foreign .d/ entry did not run"
