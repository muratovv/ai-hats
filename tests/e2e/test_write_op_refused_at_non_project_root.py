"""e2e (HATS-839): a write op (`task create`) issued from a NON-project root must
refuse and bootstrap no phantom `.agent/` tracker.

`ai_hats_dir()` used to `mkdir` unconditionally, so resolving against a
wrong-but-alive root silently resurrected an empty `.agent/ai-hats` skeleton (the
id-collision engine behind HATS-788). `ensure_ai_hats_dir` now refuses a root with no
marker (`.agent/` | `ai-hats.yaml` | `AI_HATS_DIR`).

Pin the spawned binary to THIS checkout's `src` (HATS-685) — the autouse
`_scrub_redirect_env` strips PYTHONPATH so a raw env copy would resolve the editable
install (MAIN). `parents[2]` is the repo root, so it stays portable post-merge.

Fail-under-revert: without the gate, `task create` at a bare dir prints the created
card and materializes `.agent/ai-hats/...` there.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ai_hats(binary: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    from _helpers.env import checkout_pythonpath

    env = dict(os.environ)
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT)
    env["AI_HATS_VENV"] = str(Path(sys.executable).parent.parent)
    env.pop("AI_HATS_DIR", None)  # AI_HATS_DIR would opt-in the gate — keep it unset
    return subprocess.run(
        [str(binary), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_task_create_refused_at_non_project_root(tmp_project, tmp_path):
    binary = tmp_project.ai_hats_binary
    stray = tmp_path / "stray"  # bare: no .agent/, no ai-hats.yaml, not a git repo
    stray.mkdir()

    res = _ai_hats(binary, "task", "create", "X", "--id", "X-1", cwd=stray)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "not an ai-hats project" in combined, combined
    assert not (stray / ".agent").exists(), "phantom .agent/ bootstrapped at a stray root"


def test_task_create_still_works_in_onboarded_project(tmp_project):
    """Complement: a valid onboarded project (tmp_project writes ai-hats.yaml) still creates."""
    binary = tmp_project.ai_hats_binary
    res = _ai_hats(binary, "task", "create", "OK", "--id", "OK-1", cwd=tmp_project.path)
    assert res.returncode == 0, res.stdout + res.stderr
