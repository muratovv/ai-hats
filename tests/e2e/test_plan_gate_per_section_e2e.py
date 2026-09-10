"""e2e (HATS-635, HATS-1263)

flow:   a developer attempting to transition a task to execute when required plan
        sections are missing content
cmds:
    rack transition HATS-001 execute
expect: transition to execute is blocked with exit code 1 and stderr lists the exact
        required plan sections that are empty
why:    incomplete task plans must be rejected before worktree creation to ensure design
        requirements and verification steps are documented
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG

# smoke: also run by the merge-to-master CI gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke, pytest.mark.rack]


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC = REPO_ROOT / "src"


def _run_rack(
    project_dir: Path, *args: str, timeout: float = 30.0
) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats_rack <args>`` against the current checkout."""
    env = os.environ.copy()
    from _helpers.env import checkout_pythonpath

    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, existing_pp)
    # Consent-gated on rack; this test is about the section gate behind it.
    env["AI_HATS_PLAN_ACK"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Role-less ai-hats project (no git — the block path never reaches
    worktree setup)."""
    p = tmp_path / "project"
    p.mkdir()
    ProjectConfig(provider="claude", library_paths=[]).save(p / PROJECT_CONFIG)
    Assembler(p).init()
    return p


_PARTIAL_PLAN = (
    "# Plan for HATS-001: Probe\n\n"
    "## Requirements\nOnly this section is filled.\n\n"
    "## Scope & Out-of-scope\n\n"
    "## Steps\n\n"
    "## Verification Protocol\n\n"
)


def test_transition_execute_blocks_and_names_empty_sections(project: Path) -> None:
    r = _run_rack(project, "create", "Probe", "--id", "HATS-001")
    assert r.returncode == 0, f"create failed: {r.stderr}"
    r = _run_rack(project, "transition", "HATS-001", "plan")
    assert r.returncode == 0, f"transition plan failed: {r.stderr}"

    plan_path = (
        project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / "HATS-001" / "plan.md"
    )
    assert plan_path.exists(), f"expected scaffold at {plan_path}"
    plan_path.write_text(_PARTIAL_PLAN)

    r = _run_rack(project, "transition", "HATS-001", "execute")
    # rack renders a subscriber abort on stderr (cli_common.py:70-75).
    out = r.stdout + r.stderr
    assert r.returncode != 0, (
        f"gate must BLOCK execute on a partial plan; got exit 0\noutput:\n{out}"
    )
    # The block message must NAME each empty required section...
    for marker in (
        "Empty required section(s)",
        "Scope & Out-of-scope",
        "Steps",
        "Verification Protocol",
    ):
        assert marker in out, f"missing marker {marker!r} in:\n{out}"
    # ...and must NOT list the one section that IS filled.
    assert "Requirements," not in out, f"filled section must not be listed as empty:\n{out}"
