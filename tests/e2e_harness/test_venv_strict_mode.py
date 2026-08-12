"""The e2e harness's strict-venv switch (HATS-645).

Subject: ``_helpers.venv.venv_unavailable`` — the branch deciding whether a
missing venv skips the tier or fails it. The seam that drives a REAL pytest run
through the same lever stays in ``tests/e2e/test_venv_strict_mode.py``: it
spawns a subprocess, this file does not.
"""

from __future__ import annotations

import pytest

from _helpers.venv import REQUIRE_VENV_ENV, venv_unavailable


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
