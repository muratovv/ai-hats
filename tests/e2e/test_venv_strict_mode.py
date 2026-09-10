"""e2e (HATS-645)

flow:   a maintainer running the e2e test suite gate under strict venv requirements mode
cmds:
    bash scripts/run-e2e-gate.sh
expect: missing or unbuildable test venvs raise fatal failures under strict mode instead
        of
        skipping tests
why: without strict venv mode in CI gates, environment setup failures silently skip e2e
     test suites
        and pass false green"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.venv import REQUIRE_VENV_ENV

pytestmark = pytest.mark.gates


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# A representative venv-tier test: it requests ``_shared_launcher_venv`` (via the
# ``shared_launcher`` fixture). The seam tests drive the REAL fixture through it.
# If this nodeid is ever renamed/removed, the seam tests fail loudly (the
# outcome-marker assertions below catch a miscollection) — update it here.
VENV_TIER_PROBE = (
    "tests/e2e/test_runtime_hook_propagation.py::test_e2e_skill_runtime_hook_wired_and_materialized"
)


def _run_probe_offline(tmp_path, *, strict: bool) -> subprocess.CompletedProcess[str]:
    """Run the probe test in a forced-offline subprocess pytest.

    Empty ``PATH`` ⇒ ``shutil.which("uv")`` is None ⇒ ``network_available()``
    returns False ⇒ ``_shared_launcher_venv`` hits its first branch and calls
    ``venv_unavailable`` WITHOUT attempting a build. ``strict`` controls the
    fail-closed env. ``REQUIRE_VENV_ENV`` is popped first so the non-strict case
    is honest even when the parent (the master gate) exported it.
    """
    empty_bin = tmp_path / "nopip_bin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)  # no uv ⇒ network_available() False
    env.pop(REQUIRE_VENV_ENV, None)
    # HATS-1661: `-p no:xdist` below makes the gate's `-n8` unparsable, not slow.
    env.pop("PYTEST_ADDOPTS", None)
    if strict:
        env[REQUIRE_VENV_ENV] = "1"
    return subprocess.run(
        # --tb=line surfaces the fixture-ERROR message (the fail-closed marker)
        # in the captured output; --tb=no would hide it.
        [
            sys.executable,
            "-m",
            "pytest",
            VENV_TIER_PROBE,
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "-q",
            "--no-header",
            "--tb=line",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.integration
def test_seam_strict_offline_blocks_real_pytest(tmp_path):
    """Strict env + unbuildable venv → a REAL pytest run exits non-zero.

    This is the contract the whole task turns on: the gate exports the env, the
    fixture fails-closed, pytest exits non-zero, the gate blocks. Asserting the
    ``fail-closed`` marker (not just non-zero) guards against a renamed probe
    silently passing via a collection error.
    """
    res = _run_probe_offline(tmp_path, strict=True)
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"strict+offline must block (non-zero):\n{out}"
    assert "fail-closed (HATS-645)" in out, (
        f"expected fail-closed marker (probe may have miscollected):\n{out}"
    )


@pytest.mark.integration
def test_seam_nonstrict_offline_skips_real_pytest(tmp_path):
    """No strict env + unbuildable venv → a REAL pytest run exits 0 (skips).

    The flip side: without the env the offline path degrades gracefully, so an
    offline dev still gets a green local suite. ``REQUIRE_VENV_ENV`` is popped in
    the helper so this holds even under the gate (which exports it). Exit-0 is
    exactly the false-green the strict case above closes — proving the env var
    is the sole lever.
    """
    res = _run_probe_offline(tmp_path, strict=False)
    out = res.stdout + res.stderr
    assert res.returncode == 0, f"offline w/o strict must skip (exit 0):\n{out}"
    assert "skipped" in out.lower(), f"expected a skip (probe may have miscollected):\n{out}"
