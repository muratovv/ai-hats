"""HATS-526 — N parallel ``config customize`` calls lose no additions.

The command's read-modify-write had no mutual exclusion — last writer wins,
earlier additions silently vanished (plan-stage PoC kept 1 of 3);
``locked_path`` around the RMW serializes them. Real-binary pattern mirrors
``test_task_description_file.py``. dev_rule_e2e_gate: this is the gated test;
fail-under-revert — drop the ``locked_path`` wrapping in ``customize`` and
the all-N assertions fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
N = 5


def _spawn_hats(
    project_dir: Path, *args: str, extra_env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """Start ``python -m ai_hats <args>`` against the current checkout's src."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "ai_hats", *args],
        cwd=str(project_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )


def _drain(procs: list[subprocess.Popen[str]]) -> None:
    outcomes = [(p.args, p.communicate(timeout=120)[0], p.returncode) for p in procs]
    failed = [(args, out) for args, out, code in outcomes if code != 0]
    assert not failed, f"customize calls failed: {failed}"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "project"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(proj / "ai-hats.yaml")
    Assembler(proj).init()
    return proj


def test_parallel_global_customize_keeps_all_additions(
    project: Path, tmp_path: Path
) -> None:
    user_home = tmp_path / "user-home"
    extra_env = {"AI_HATS_USER_HOME": str(user_home)}

    procs = [
        _spawn_hats(
            project, "config", "customize", f"role-{i}",
            "--add-trait", f"trait-{i}", "--global",
            extra_env=extra_env,
        )
        for i in range(N)
    ]
    _drain(procs)

    on_disk = yaml.safe_load(
        (user_home / ".ai-hats" / "customizations.yaml").read_text()
    )
    roles = on_disk["customizations"]
    assert sorted(roles) == [f"role-{i}" for i in range(N)], (
        f"lost update: expected all {N} roles, got {sorted(roles)}"
    )
    for i in range(N):
        assert roles[f"role-{i}"]["add"]["traits"] == [f"trait-{i}"]


def test_parallel_project_customize_keeps_all_additions(project: Path) -> None:
    procs = [
        _spawn_hats(
            project, "config", "customize", f"role-{i}",
            "--add-trait", f"trait-{i}",
        )
        for i in range(N)
    ]
    _drain(procs)

    on_disk = yaml.safe_load((project / "ai-hats.yaml").read_text())
    roles = on_disk["customizations"]
    assert sorted(roles) == [f"role-{i}" for i in range(N)], (
        f"lost update: expected all {N} roles, got {sorted(roles)}"
    )
