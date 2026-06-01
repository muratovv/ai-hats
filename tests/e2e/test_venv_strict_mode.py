"""HATS-645: ``AI_HATS_E2E_REQUIRE_VENV`` converts a venv-tier skip into a fail.

Unit-level proof of the fail-closed decision in
:func:`tests.e2e._helpers.venv.venv_unavailable`. The session venv fixture
(``_shared_launcher_venv``) routes every "cannot build the venv" path through
this helper; under the master gate's strict env it must FAIL (so the gate
blocks the push), otherwise SKIP (so an offline dev still gets a green local
suite — the "degrade, not cascade" contract).

Fast unit test: no venv build, no network, deliberately NOT marked
``integration`` — it exercises pure branch logic, so it runs in the normal
suite rather than under the (heavy) gate selection.
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
