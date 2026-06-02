"""HATS-645: ``AI_HATS_E2E_REQUIRE_VENV`` converts a venv-tier skip into a fail.

Two layers:

* **Unit** (``test_*_skips`` / ``*_to_failure``) — the fail-closed *decision* in
  :func:`tests.e2e._helpers.venv.venv_unavailable`, exercised directly. Fast, no
  subprocess, deliberately NOT marked ``integration``.
* **Seam** (``test_seam_*``) — the integration the unit layer can't reach: that
  a REAL ``pytest`` run, driving the REAL ``_shared_launcher_venv`` fixture, exits
  **non-zero under strict mode** (so the gate blocks) and **zero without it** (so
  an offline dev still gets a green local suite). This is the contract the whole
  task turns on — offline ⇒ gate blocks instead of false-greening — and it only
  exists at the pytest-process boundary, so it is a real-subprocess
  ``@pytest.mark.integration`` test. The venv is forced unbuildable
  deterministically (empty ``PATH`` ⇒ ``network_available()`` False), so the
  fixture short-circuits at its first branch — no actual build, ~instant.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.venv import REQUIRE_VENV_ENV, venv_unavailable


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
# A representative venv-tier test: it requests ``_shared_launcher_venv`` (via the
# ``shared_launcher`` fixture). The seam tests drive the REAL fixture through it.
# If this nodeid is ever renamed/removed, the seam tests fail loudly (the
# outcome-marker assertions below catch a miscollection) — update it here.
VENV_TIER_PROBE = (
    "tests/e2e/test_runtime_hook_propagation.py"
    "::test_e2e_skill_runtime_hook_wired_and_materialized"
)


def _run_probe_offline(tmp_path, *, strict: bool) -> subprocess.CompletedProcess[str]:
    """Run the probe test in a forced-offline subprocess pytest.

    Empty ``PATH`` ⇒ ``shutil.which("pip")`` is None ⇒ ``network_available()``
    returns False ⇒ ``_shared_launcher_venv`` hits its first branch and calls
    ``venv_unavailable`` WITHOUT attempting a build. ``strict`` controls the
    fail-closed env. ``REQUIRE_VENV_ENV`` is popped first so the non-strict case
    is honest even when the parent (the master gate) exported it.
    """
    empty_bin = tmp_path / "nopip_bin"
    empty_bin.mkdir()
    env = dict(os.environ)
    env["PATH"] = str(empty_bin)  # no pip/pip3 ⇒ network_available() False
    env.pop(REQUIRE_VENV_ENV, None)
    if strict:
        env[REQUIRE_VENV_ENV] = "1"
    return subprocess.run(
        # --tb=line surfaces the fixture-ERROR message (the fail-closed marker)
        # in the captured output; --tb=no would hide it.
        [sys.executable, "-m", "pytest", VENV_TIER_PROBE,
         "-p", "no:xdist", "-p", "no:cacheprovider", "-q", "--no-header", "--tb=line"],
        cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=120,
    )


def test_strict_mode_converts_skip_to_failure(monkeypatch):
    """Gate sets the env to "1" → a missing venv is a FAILURE, not a skip.

    Explicit skip/fail discrimination (not ``pytest.raises``): a regression
    that reverts the env check makes ``venv_unavailable`` *skip*, and a bare
    ``pytest.raises(Failed)`` would let that Skipped propagate and mark this
    test *skipped* — amber, not red. Converting the skip into an explicit
    ``pytest.fail`` here guarantees the regression shows up as RED.
    """
    monkeypatch.setenv(REQUIRE_VENV_ENV, "1")
    try:
        venv_unavailable("offline / no warm pip cache")
    except pytest.skip.Exception:
        pytest.fail("strict mode (env=1) must FAIL the venv tier, not skip it")
    except pytest.fail.Exception as exc:
        # The message names the fail-closed reason so a blocked maintainer sees why.
        assert "fail-closed" in str(exc)
    else:
        pytest.fail("venv_unavailable must not return normally")


def test_non_strict_mode_skips(monkeypatch):
    """Env unset (normal local run) → graceful skip, suite stays green."""
    monkeypatch.delenv(REQUIRE_VENV_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        venv_unavailable("offline / no warm pip cache")


def test_env_set_to_other_value_still_skips(monkeypatch):
    """Only the literal "1" arms strict mode; any other value degrades."""
    monkeypatch.setenv(REQUIRE_VENV_ENV, "0")
    with pytest.raises(pytest.skip.Exception):
        venv_unavailable("offline")


# --------------------------- seam (real pytest run) ---------------------------


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
