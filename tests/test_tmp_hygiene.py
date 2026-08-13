"""Behavioural tests for the HATS-570 tmp-dir hygiene machinery.

Two contracts of the autouse ``_wt_sandbox`` session fixture (the
repo-root ``conftest.py``):

1. **Redirect is live** — ``tempfile.gettempdir()`` (and therefore any
   ``ai-hats-wt-*`` ``mkdtemp`` that still reaches it) points at a
   pytest-owned ``wt-sandbox`` dir, NOT the real temp root. Since HATS-1632
   that is the bare-core fallback only — the ai-hats road mints under
   ``worktree_checkouts_dir``, guarded by the cache-home sandbox instead.
2. **Pass-only gate** — a GREEN session sweeps the sandbox; a session
   with failures PRESERVES it for triage. Exercised via ``pytester``
   running the REAL (copied-verbatim) conftest in an isolated subprocess
   so we assert the actual fixture, not a re-implementation.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

_CONFTEST = Path(__file__).resolve().parent.parent / "conftest.py"


def test_redirect_is_live() -> None:
    """gettempdir() resolves into the session sandbox (caching defeated)."""
    assert Path(tempfile.gettempdir()).name.startswith("wt-sandbox"), (
        f"expected a wt-sandbox temp root, got {tempfile.gettempdir()!r}"
    )


def test_worktree_mkdtemp_lands_in_sandbox() -> None:
    """The exact call worktree.py uses (mkdtemp, ai-hats-wt- prefix) is
    redirected — proves the production leak path is covered in-process."""
    d = Path(tempfile.mkdtemp(prefix="ai-hats-wt-probe-"))
    try:
        assert str(d).startswith(tempfile.gettempdir())
        assert Path(tempfile.gettempdir()).name.startswith("wt-sandbox")
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.integration
def test_a_chdir_into_tmp_path_does_not_take_the_session_down(pytester) -> None:
    """One test standing in its own reaped tmp_path must not fail the next one.

    Under ``tmp_path_retention_policy=failed`` the dir goes at teardown, and a
    process left inside it gets FileNotFoundError from every later os.getcwd()
    — 59 failures and 933 errors when it happened for real (HATS-1624).
    """
    pytester.makeconftest(_CONFTEST.read_text())
    pytester.makepyfile(
        """
        import os

        def test_a_wanders_off(tmp_path):
            os.chdir(tmp_path)          # no restore — the shape that cascaded

        def test_b_still_runs(tmp_path, monkeypatch):
            monkeypatch.chdir(tmp_path)
        """
    )
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", "-o", "tmp_path_retention_policy=failed"
    )
    result.assert_outcomes(passed=2)


@pytest.mark.integration
def test_failed_session_preserves_sandbox(pytester, tmp_path) -> None:
    pytester.makeconftest(_CONFTEST.read_text())
    rec = tmp_path / "sandbox_path.txt"
    pytester.makepyfile(
        f"""
        import tempfile
        from pathlib import Path

        def test_record_then_fail():
            Path(r{str(rec)!r}).write_text(tempfile.gettempdir())
            assert False, "force failure to exercise the preserve path"
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    sandbox = Path(rec.read_text())
    assert sandbox.exists(), "a failed session MUST preserve the wt-sandbox"


@pytest.mark.integration
def test_green_session_sweeps_sandbox(pytester, tmp_path) -> None:
    pytester.makeconftest(_CONFTEST.read_text())
    rec = tmp_path / "sandbox_path.txt"
    pytester.makepyfile(
        f"""
        import tempfile
        from pathlib import Path

        def test_record_then_pass():
            Path(r{str(rec)!r}).write_text(tempfile.gettempdir())
            assert True
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    sandbox = Path(rec.read_text())
    assert not sandbox.exists(), "a green session MUST sweep the wt-sandbox"
