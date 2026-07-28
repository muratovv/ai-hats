"""e2e: ``python -m ai_hats_rack`` is a working rack entry point (HATS-1263).

The rack's only console script (``rack``) materialises inside a built venv, but
the e2e shim tier drives interpreters directly — so it needs the same
``python -m`` affordance ``ai_hats`` already has.

Fail-under-revert: delete ``ai_hats_rack/__main__.py`` → exits non-zero with
"No module named ai_hats_rack.__main__".
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``python -m ai_hats_rack <args>`` against THIS checkout.

    PYTHONPATH is explicit: under a worktree the editable install still resolves
    to the main checkout, so an inherited env would test the wrong source.
    """
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_python_m_ai_hats_rack_is_runnable():
    res = _run("--help")

    assert res.returncode == 0, (
        "`python -m ai_hats_rack` must expose the rack CLI — the shim tier has "
        f"no `rack` console script.\nstdout: {res.stdout[-500:]}\n"
        f"stderr: {res.stderr[-500:]}"
    )
    assert "No module named" not in res.stderr, res.stderr


def test_python_m_ai_hats_rack_mounts_the_real_verbs():
    """A __main__ that merely imports cleanly is not enough — it must be `main`."""
    res = _run("--help")

    missing = [v for v in ("create", "transition", "context", "ls") if v not in res.stdout]
    assert not missing, (
        f"`python -m ai_hats_rack --help` is missing verbs {missing} — it is not "
        f"wired to `ai_hats_rack.cli:main`.\nstdout: {res.stdout[-800:]}"
    )
