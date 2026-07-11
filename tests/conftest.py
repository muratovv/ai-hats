"""Shared pytest fixtures for the ai-hats test suite.

HATS-470: :mod:`ai_hats_core.safe_delete` keeps a per-process trash session
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


@pytest.fixture(scope="session", autouse=True)
def _real_repo_integrity_tripwire():
    """Fail the session loud if any test mutated the real repo (HATS-887).

    Snapshots the checked-out HEAD + this worktree's index at session start and
    asserts them unchanged at session end, naming the delta — catches the "a test
    wrote real ``.git``" class. Deliberately not all-refs: a sibling branch moved
    by a concurrent agent in a shared clone must not trip it. Watched root defaults
    to this repo; ``AI_HATS_REPO_INTEGRITY_ROOT`` overrides it (pytester self-test).
    """
    import os
    from pathlib import Path

    from tests._repo_integrity import diff_repo, snapshot_repo

    override = os.environ.get("AI_HATS_REPO_INTEGRITY_ROOT")
    root = Path(override) if override else Path(__file__).resolve().parent.parent
    before = snapshot_repo(root)
    yield
    if not before.is_repo:
        return
    delta = diff_repo(before, snapshot_repo(root))
    if delta is not None:
        pytest.fail(
            f"[repo-integrity] a test mutated the real repo at {root}: {delta}",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _reset_safe_delete_session(monkeypatch):
    """Reset trash-bin module state + clear AI_HATS_TRASH_DIR per test.

    Runs for EVERY test (autouse) to guarantee that ``safe_delete``
    behaves as if it just loaded. No yields/teardowns needed beyond the
    final reset because module state is process-local and tests don't
    fork.
    """
    from ai_hats_core import safe_delete

    safe_delete.reset_session()
    monkeypatch.delenv(safe_delete.ENV_TRASH_DIR, raising=False)
    yield
    safe_delete.reset_session()


@pytest.fixture(autouse=True)
def _isolate_ai_hats_dir(monkeypatch):
    """Neutralize an ambient ``AI_HATS_DIR`` for EVERY test (HATS-671).

    ``ai_hats_dir()`` gives the ``AI_HATS_DIR`` env var precedence over the
    caller's ``project_dir`` (intended for out-of-tree data dirs, HATS-380/395).
    A test that does not set the env explicitly therefore *escapes* its
    ``tmp_path`` when pytest is launched in a shell that exports
    ``AI_HATS_DIR`` — e.g. ``test_save_artifact_expands_ai_hats_dir_placeholder``
    wrote the literal ``"payload"`` into the real
    ``$AI_HATS_DIR/sessions/retros/judge/`` (5 corrupt 7-byte reports, HATS-671).

    Clearing it autouse forces every test to resolve under its own
    ``tmp_path`` / ``project_dir``. Tests that genuinely exercise the override
    re-set it via ``monkeypatch.setenv`` (runs after this clear, undone at
    teardown), so they are unaffected.
    """
    # raw names on purpose: pytester copies this conftest into a tmp dir where
    # ai_hats resolves to the editable install, which may predate the constants
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)  # HATS-897 pair var
    yield


@pytest.fixture(autouse=True)
def _isolate_git_env(monkeypatch):
    """Strip inherited ``GIT_*`` plumbing vars for EVERY test (HATS-886).

    The smoke/integration gate runs inside a ``git merge`` (merge-smoke, on
    ``wt merge`` / ``transition done``), where git exports ``GIT_DIR`` /
    ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` at the REAL repo. Any test that shells
    ``git`` while inheriting ``os.environ`` would then operate on the real
    ``.git`` — observed committing an ``init`` onto real ``master``. Clearing
    them makes every git-invoking test resolve its own ``cwd`` repo. Tests that
    deliberately set one (the HATS-886 regression gate) re-``setenv`` after this.
    """
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_ai_hats_user_home(monkeypatch, tmp_path):
    """Pin ``AI_HATS_USER_HOME`` to an empty per-test dir for EVERY test (HATS-814).

    ``UserConfig.default_path()`` resolves
    ``<user_home>/.ai-hats/customizations.yaml`` where ``user_home()`` falls back
    to the real ``Path.home()`` when ``AI_HATS_USER_HOME`` is unset (HATS-532). A
    composing test therefore reads the developer's PERSONAL ``~/.ai-hats`` global
    layer — non-hermetic, and after the HATS-814 leftover-sidecar guard it turns
    local runs RED on any machine whose ``ai-hats-custom`` skills still ship
    hook-bearing ``metadata.yaml`` (not yet migrated by HATS-816). Pinning an
    empty home makes the suite hermetic = CI (which has no ``~/.ai-hats``). Tests
    that genuinely exercise the global layer re-set ``AI_HATS_USER_HOME`` via
    ``monkeypatch.setenv`` (runs after this, undone at teardown), so they are
    unaffected.
    """
    home = tmp_path / "_ai_hats_user_home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("AI_HATS_USER_HOME", str(home))
    yield


@pytest.fixture(autouse=True)
def _isolate_session_env(monkeypatch):
    """Clear ambient ``AI_HATS_SESSION_ID`` / ``AI_HATS_ROOT_PID`` per test (HATS-982).

    The HATS-955 single-slot ownership check reads ``AI_HATS_SESSION_ID`` from
    ``os.environ``; run inside a live ai-hats session (which exports both), state
    tests that drive cross-task transitions hit ``OwnershipRefused`` — failures
    absent in CI. Clearing them makes every test resolve with no ambient session,
    as CI does. Tests that need an identity re-``setenv`` after this (runs first,
    undone at teardown), so they are unaffected.
    """
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)
    yield
