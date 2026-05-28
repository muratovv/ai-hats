"""Shared pytest fixtures for the ai-hats test suite.

HATS-470: :mod:`ai_hats.safe_delete` keeps a per-process trash session
in module-level state. Without an autouse reset, the first test to
trigger a destructive op pins the session for every subsequent test,
which corrupts assertions about default vs custom trash base, manifest
content, and the under-trash recursion guard.

HATS-570: worktrees are born via ``tempfile.mkdtemp`` in
``worktree.py`` (``ai-hats-wt-*`` prefix), which honours
``tempfile.gettempdir()`` / ``TMPDIR``. Both in-process ``mgr.create()``
and subprocess ``ai-hats wt create`` (which copy ``os.environ``) leak
those dirs into the real temp root unless redirected. The
:func:`_wt_sandbox` session fixture redirects BOTH into a pytest-owned
sandbox and sweeps it on a green run.
"""
from __future__ import annotations

import shutil
import tempfile

import pytest

# HATS-570: enable the ``pytester`` fixture (not on by default) so
# tests/test_tmp_hygiene.py can drive isolated inner pytest sessions to
# assert the pass-only sweep gate.
pytest_plugins = ["pytester"]


# HATS-570 (S1) — stash per-phase reports on the item so fixtures can
# read test outcome. Standard pytest recipe; enables the pass-only
# cleanup gating used by the venv-tier finalizer (tests/e2e/conftest.py).
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):  # noqa: ANN001, ANN201
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)  # rep_setup / rep_call / rep_teardown


@pytest.fixture(scope="session", autouse=True)
def _wt_sandbox(tmp_path_factory, request):
    """Redirect every ``ai-hats-wt-*`` worktree birth into a session-owned
    sandbox so they never pollute the real temp root (HATS-570).

    setup    : a fresh sandbox — self-heals any leftovers a prior crashed
               run might have left (cleanup is idempotent by construction).
    teardown : ``rmtree`` ONLY on a fully-green session. A session with
               failures keeps the sandbox and prints its path so the
               worktree artefacts survive for triage.

    Redirects BOTH worktree creation paths:

    * in-process ``mgr.create()`` — ``tempfile.gettempdir()`` memoises
      into ``tempfile.tempdir`` on first use, so patching that cached
      attribute is REQUIRED; ``setenv`` alone would be too late.
    * subprocess CLI ``wt create`` — tests run ``env = os.environ.copy()``
      so ``TMPDIR`` rides along into the child's ``mkdtemp``.
    """
    sandbox = tmp_path_factory.mktemp("wt-sandbox")
    mp = pytest.MonkeyPatch()
    mp.setattr(tempfile, "tempdir", str(sandbox))
    mp.setenv("TMPDIR", str(sandbox))
    try:
        yield sandbox
    finally:
        mp.undo()
        if request.session.testsfailed == 0:
            shutil.rmtree(sandbox, ignore_errors=True)
        else:
            print(
                f"\n[wt-sandbox] {request.session.testsfailed} failure(s) — "
                f"worktree artefacts preserved for triage: {sandbox}"
            )


@pytest.fixture(autouse=True)
def _reset_safe_delete_session(monkeypatch):
    """Reset trash-bin module state + clear AI_HATS_TRASH_DIR per test.

    Runs for EVERY test (autouse) to guarantee that ``safe_delete``
    behaves as if it just loaded. No yields/teardowns needed beyond the
    final reset because module state is process-local and tests don't
    fork.
    """
    from ai_hats import safe_delete

    safe_delete.reset_session()
    monkeypatch.delenv(safe_delete.ENV_TRASH_DIR, raising=False)
    yield
    safe_delete.reset_session()
