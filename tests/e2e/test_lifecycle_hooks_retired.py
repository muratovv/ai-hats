"""e2e (HATS-1147)

flow:   a developer initializing a project after lifecycle hooks retirement
cmds:
    ai-hats self init -r assistant -p claude
expect: session initialization materializes runtime tool hooks while skipping retired
        lifecycle hooks
why: without lifecycle hook retirement, deprecated hook types generate unnecessary
     settings.json noise"""

from __future__ import annotations
from _helpers.git import git as _git

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = [pytest.mark.integration, pytest.mark.library]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "lifecycle_tombstone_lib"


def _run_rack(project_dir: Path, *args: str, timeout: float = 60.0):
    """Run ``python -m ai_hats_rack <args>`` against the current checkout."""
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    env["AI_HATS_PLAN_ACK"] = "1"  # consent gate is not what this test is about
    return subprocess.run(  # noqa: S603 — fixed argv, test helper
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def tombstone_launcher(shared_launcher, tmp_path_factory):
    """Session venv with a clean env (HATS-685/582): pop PYTHONPATH so the
    launcher does not import the source tree without ``library/``, and isolate
    HOME so no dev ``~/.ai-hats/`` leaks in."""
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("lifecycle-tombstone-home"))
    return launcher, env, shared_venv


def test_declaration_fails_composition_by_name(tombstone_launcher, tmp_path):
    """A fixture skill declaring the retired channel makes a real
    ``ai-hats self init`` fail, naming the skill and the card."""
    launcher, env, _venv = tombstone_launcher
    project = tmp_path / "project"
    project.mkdir(parents=True)
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "init")
    shutil.copytree(FIXTURE_LIB, project / "libraries")

    res = subprocess.run(  # noqa: S603 — fixed argv, test helper
        [
            str(launcher),
            "self",
            "init",
            "-p",
            "claude",
            "-r",
            "e2e-lifecycle-tombstone-role",
            "--no-wizard",
            "--task-prefix",
            "TST",
        ],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    combined = res.stdout + res.stderr
    assert res.returncode != 0, (
        "a lifecycle_hooks: declaration must FAIL composition, not be ignored — "
        f"init exited 0.\noutput:\n{combined}"
    )
    assert "e2e-lifecycle-tombstone" in combined, (
        f"the failure must NAME the offending skill:\n{combined}"
    )
    assert "HATS-1147" in combined, f"the failure must cite the retirement card:\n{combined}"


def test_rack_transition_unaffected_and_no_lifecycle_tree(tmp_path):
    """plan → execute still walks, and the retired managed tree never appears."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "e2e@test")
    _git(project, "config", "user.name", "E2E")
    (project / "README.md").write_text("# e2e\n")
    _git(project, "add", "README.md")
    _git(project, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    ProjectConfig(provider="claude", library_paths=[]).save(project / PROJECT_CONFIG)
    Assembler(project).init()

    r = _run_rack(project, "create", "Probe", "--id", "HATS-001")
    assert r.returncode == 0, f"create failed:\n{r.stdout}\n{r.stderr}"
    r = _run_rack(project, "transition", "HATS-001", "plan")
    assert r.returncode == 0, f"transition plan failed:\n{r.stdout}\n{r.stderr}"

    tasks = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    (tasks / "HATS-001" / "plan.md").write_text(
        "# Plan\n\n## Requirements\nr\n\n## Scope & Out-of-scope\ns\n\n"
        "## Steps\n- [ ] do\n\n## Verification Protocol\npytest\n"
    )
    r = _run_rack(project, "transition", "HATS-001", "execute")
    assert r.returncode == 0, f"transition execute failed:\n{r.stdout}\n{r.stderr}"

    retired_tree = project / ".agent" / "ai-hats" / "tracker" / "lifecycle-hooks"
    assert not retired_tree.exists(), f"the retired managed tree was materialized at {retired_tree}"
