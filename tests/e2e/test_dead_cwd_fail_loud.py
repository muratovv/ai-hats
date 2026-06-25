"""e2e (HATS-788): running `ai-hats` from a REMOVED cwd fails loud instead of
silently resurrecting a phantom `.agent/` tracker (Linux) or crashing with a
raw traceback (macOS).

A removed cwd is the aftermath of the headline bug: a worktree torn down out
from under the operator's shell. `_project_dir` must raise `DeadCwdError` (a
clean ClickException), never reach `ai_hats_dir()`'s unconditional `mkdir -p`.

Fail-under-revert: without the dead-cwd guard, on Linux the run exits 0 with a
phantom `.agent/` recreated under the dead path; on macOS it dumps a traceback.
"""

from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.integration


def test_dead_cwd_fails_loud_without_phantom(tmp_project, tmp_path):
    binary = tmp_project.ai_hats_binary

    dead = tmp_path / "dead"
    dead.mkdir()

    # cd into `dead`, remove it, then exec ai-hats from the now-removed cwd.
    # `rmdir` of one's own cwd is permitted on Linux/macOS; skip elsewhere.
    script = 'cd "$1" && rmdir "$1" && exec "$2" task list'
    try:
        res = subprocess.run(
            ["bash", "-c", script, "_", str(dead), str(binary)],
            env={**os.environ},
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.SubprocessError as exc:  # pragma: no cover - platform guard
        pytest.skip(f"cannot exercise removed-cwd on this platform: {exc}")

    combined = (res.stdout + res.stderr).lower()
    assert res.returncode != 0, combined
    assert "no longer exists" in combined or "cd to your project" in combined, combined
    # The phantom would re-create the removed dir via `mkdir -p`; it must stay gone.
    assert not dead.exists(), "fail-loud must not resurrect the dead cwd / a phantom .agent"
