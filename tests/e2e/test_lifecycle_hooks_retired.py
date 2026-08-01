"""E2E (HATS-1147): the ``lifecycle_hooks`` channel is retired.

Two halves, matching the two ways the retirement can regress.

**The tombstone fires.** A skill declaring ``lifecycle_hooks:`` must fail
composition by name, through the real binary. This is the load-bearing half:
the zero-declaration survey covered only the layers we can see, so a
third-party layer must not be able to ship an edge gate that silently never
installs (the HYP-078 hole ADR-0019 D8 closes by tombstone rather than by a
deprecation window).

**The rack is untouched.** Every FSM edge used to run
``HookRunnerExtension._assert_manifest_intact`` in-lock. With the channel gone
a real ``rack transition`` still walks plan → execute, and no
``tracker/lifecycle-hooks/`` tree appears.

Fail-under-revert: drop the ``lifecycle_hooks`` raise from
``SkillMetadata.from_skill_dir`` and ``self init`` composes the fixture role
silently → ``test_declaration_fails_composition_by_name`` goes red.

Per dev_rule_e2e_gate: real bash + real pip + real ``ai-hats`` binary for the
composition half; the rack half runs the real ``ai_hats_rack`` CLI against the
current checkout (the ``test_plan_gate_per_section_e2e`` pattern — an editable
install would resolve the main checkout, not this worktree).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_LIB = REPO_ROOT / "tests" / "fixtures" / "lifecycle_tombstone_lib"


def _git(cwd: Path, *args: str):
    return subprocess.run(  # noqa: S603 — fixed argv, test helper
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


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
