"""e2e (HATS-839): a write op (`rack create`) issued from a NON-project root must
refuse and bootstrap no phantom `.agent/` tracker.

Re-pointed off the legacy ``ai-hats task`` CLI (HATS-1263); the gate now lives in
``ai_hats_rack.resolver``, which raises `NoProjectRootError` on a marker-less root
(`.agent/` | `ai-hats.yaml`) with zero side effects — an eager `mkdir` on a
mis-resolved root is how stray trackers were born (HATS-788).

Explicit PYTHONPATH pins the spawned interpreter to THIS checkout (HATS-685):
the autouse `_scrub_redirect_env` strips it, so a raw env copy would resolve the
editable install (MAIN).

Fail-under-revert: without the gate, `create` at a bare dir prints the created
card and materializes `.agent/ai-hats/...` there.
"""  # comment-length: allow

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from ai_hats_rack.cli_common import ENV_TASKS_DIR

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _rack(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env.pop(ENV_TASKS_DIR, None)  # an explicit tasks-dir bypasses the gate — keep it unset
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_task_create_refused_at_non_project_root(tmp_path):
    stray = tmp_path / "stray"  # bare: no .agent/, no ai-hats.yaml, not a git repo
    stray.mkdir()

    res = _rack("create", "X", "--id", "X-1", cwd=stray)
    combined = res.stdout + res.stderr  # rack renders refusals on stderr
    assert res.returncode != 0, combined
    assert "No project root found" in combined, combined
    assert "no ancestor holds" in combined, combined
    assert not (stray / ".agent").exists(), "phantom .agent/ bootstrapped at a stray root"


def test_task_create_still_works_in_onboarded_project(tmp_project):
    """Complement: a valid onboarded project (tmp_project writes ai-hats.yaml) still creates."""
    res = _rack("create", "OK", "--id", "HATS-1", cwd=tmp_project.path)
    assert res.returncode == 0, res.stdout + res.stderr
