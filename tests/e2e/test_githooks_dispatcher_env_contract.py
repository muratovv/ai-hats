"""e2e (HATS-1613, absorbs HATS-1525)

flow:   a developer commits in project A from a shell whose session belongs to
        project B, so the inherited AI_HATS_VENV / AI_HATS_DIR pair names B
cmds:
    git commit -m "feature"
expect: the dispatcher drops the foreign pair and runs A's own gates; a bare
        override with no pair is still honoured, and a matching pin is silent
why:    honouring a leaked pin runs the commit's gates under another project's
        interpreter — observed in HATS-1525, where the commit was refused
"""  # comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.env import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_DIR, ENV_AI_HATS_VENV
from ai_hats.paths import PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AI_HATS_PYTHON = Path(sys.executable)

pytestmark = [pytest.mark.integration, pytest.mark.guards]

GUARD = """#!/usr/bin/env bash
set -uo pipefail
touch "$(git rev-parse --show-toplevel)/.marker-guard"
exit 0
"""

# Stands in for another project's interpreter. Refusing (exit 1) is the point:
# HATS-1525 was observed as a REFUSED commit, so honouring this fails the commit
# and leaves a marker naming which python ran.
FOREIGN_PYTHON = """#!/usr/bin/env bash
touch "{marker}"
exit 1
"""


def _pinned_env() -> dict[str, str]:
    from _helpers.env import checkout_pythonpath

    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env[ENV_AI_HATS_VENV] = str(AI_HATS_PYTHON.parent.parent)
    env.pop(AI_HATS_PROJECT_DIR_ENV, None)
    env.pop(ENV_AI_HATS_DIR, None)
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


def _project(tmp_path: Path) -> Path:
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

    cp = subprocess.run(
        [
            str(AI_HATS_PYTHON),
            "-m",
            "ai_hats",
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "guard-role",
            "--no-wizard",
        ],
        cwd=str(project),
        env=_pinned_env(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert cp.returncode == 0, f"self init failed:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".githooks" / "pre-commit").is_file(), "dispatcher not installed"

    # The interpreter a real project owns. Without it, dropping a foreign pin
    # would leave the stub with nothing to fall back to, and the test would be
    # measuring "no install" rather than "used its own".
    own = project / ".agent" / "ai-hats" / ".venv" / "bin"
    own.mkdir(parents=True, exist_ok=True)
    (own / "python").write_text(f'#!/usr/bin/env bash\nexec "{AI_HATS_PYTHON}" "$@"\n')
    (own / "python").chmod(0o755)
    return project


def _foreign_venv(root: Path, marker: Path) -> Path:
    """A venv-shaped dir whose `bin/python` refuses and says it ran."""
    venv = root / ".agent" / "ai-hats" / ".venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text(FOREIGN_PYTHON.format(marker=marker))
    python.chmod(0o755)
    return venv


def _commit(project: Path, name: str, env: dict[str, str]):
    (project / name).write_text("x\n")
    _git_ok("add", name, cwd=project, env=env)
    return _git("commit", "-m", name, "--quiet", cwd=project, env=env)


@pytest.fixture(scope="module")
def _sandbox(tmp_path_factory):
    """One real `self init`; the cases differ only by environment."""
    tmp_path = tmp_path_factory.mktemp("dispatcher-env")
    project = _project(tmp_path)
    foreign_root = tmp_path / "foreign-project"
    marker = tmp_path / ".marker-foreign-python"
    foreign_venv = _foreign_venv(foreign_root, marker)
    return project, foreign_root, foreign_venv, marker


def _reset(project: Path, marker: Path) -> None:
    (project / ".marker-guard").unlink(missing_ok=True)
    marker.unlink(missing_ok=True)


def test_a_foreign_venv_pin_is_dropped_and_this_projects_gates_run(_sandbox):
    """HATS-1525, reproduced: the pair names another project, so it is not ours."""
    project, foreign_root, foreign_venv, marker = _sandbox
    _reset(project, marker)

    env = _pinned_env() | {
        ENV_AI_HATS_VENV: str(foreign_venv),
        AI_HATS_PROJECT_DIR_ENV: str(foreign_root),
    }
    cp = _commit(project, "a.txt", env)

    assert not marker.exists(), (
        f"the commit ran another project's interpreter:\n{cp.stdout}\n{cp.stderr}"
    )
    assert cp.returncode == 0, f"commit refused:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".marker-guard").exists(), "this project's gate did not run"


def test_a_pair_pointing_out_of_tree_is_dropped_even_when_the_dir_is_in_tree(_sandbox):
    """The case that separates a paired check from a prefix one.

    ``AI_HATS_DIR`` lies UNDER this project, so the old prefix test
    (``$AI_HATS_DIR == $PROJECT_DIR/*``) accepts it — while its pair names a
    different project, which is the only thing that decides whose pin it is.
    """
    project, foreign_root, _foreign_venv, marker = _sandbox
    _reset(project, marker)

    decoy = project / ".agent" / "decoy"
    (decoy / "versions").mkdir(parents=True, exist_ok=True)
    (decoy / "versions" / "current").write_text("deadbeef\n")
    sha_venv = decoy / "versions" / "deadbeef" / "bin"
    sha_venv.mkdir(parents=True, exist_ok=True)
    python = sha_venv / "python"
    python.write_text(FOREIGN_PYTHON.format(marker=marker))
    python.chmod(0o755)

    env = _pinned_env() | {
        ENV_AI_HATS_DIR: str(decoy),
        AI_HATS_PROJECT_DIR_ENV: str(foreign_root),
    }
    env.pop(ENV_AI_HATS_VENV, None)
    cp = _commit(project, "b.txt", env)

    assert not marker.exists(), (
        "a prefix test accepted an in-tree AI_HATS_DIR whose pair is foreign:\n"
        f"{cp.stdout}\n{cp.stderr}"
    )
    assert cp.returncode == 0, f"commit refused:\n{cp.stdout}\n{cp.stderr}"


def test_a_matching_pin_is_honoured_without_noise(_sandbox):
    """Do not over-fire: a same-project pair is the normal case."""
    project, _foreign_root, _foreign_venv, marker = _sandbox
    _reset(project, marker)

    env = _pinned_env() | {AI_HATS_PROJECT_DIR_ENV: str(project)}
    cp = _commit(project, "c.txt", env)

    assert cp.returncode == 0, f"commit refused:\n{cp.stdout}\n{cp.stderr}"
    assert (project / ".marker-guard").exists(), "this project's gate did not run"
    assert "foreign" not in cp.stderr.lower(), f"guard fired on our own pin:\n{cp.stderr}"


def test_a_bare_override_with_no_pair_is_still_honoured(_sandbox):
    """env-wins survives: an unpaired key is a human's explicit choice, not a leak."""
    project, _foreign_root, foreign_venv, marker = _sandbox
    _reset(project, marker)

    env = _pinned_env() | {ENV_AI_HATS_VENV: str(foreign_venv)}
    env.pop(AI_HATS_PROJECT_DIR_ENV, None)
    cp = _commit(project, "d.txt", env)

    assert marker.exists(), (
        f"a bare override must be honoured, not re-resolved:\n{cp.stdout}\n{cp.stderr}"
    )
    assert cp.returncode != 0, "the refusing interpreter ran, so the commit must fail"
