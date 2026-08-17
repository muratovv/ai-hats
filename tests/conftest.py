"""Shared pytest fixtures for the ai-hats test suite.

HATS-470: :mod:`ai_hats_core.safe_delete` keeps a per-process trash session
in module-level state. Without an autouse reset, the first test to
trigger a destructive op pins the session for every subsequent test,
which corrupts assertions about default vs custom trash base, manifest
content, and the under-trash recursion guard.

HATS-570: ``_wt_sandbox`` lives in the repo-root conftest — it must also
reach ``packages/*/tests``.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# HATS-1429: dropped at conftest import, not in the autouse fixture below — modules
# resolve library layers at *collection* time, earlier than any fixture can reach.
# Both halves, never one: HATS-897 scopes AI_HATS_DIR *by* the pin, so dropping the
# pin alone would promote a scoped override into a global one.
for _pinned in ("AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
    os.environ.pop(_pinned, None)


@pytest.fixture(scope="session", autouse=True)
def _no_retired_prune(request):
    """Keep the retired-distribution prune out of the developer's own venv.

    HATS-1280: the prune runs from ``_bump_internal`` and uninstalls against the
    *running* interpreter — which under pytest is this checkout's venv. Any test
    that reaches that entry point without stubbing the subprocess would really
    remove the distribution. Opt back in per-test by deleting the var.
    """
    try:
        from ai_hats.retired_dists import ENV_SKIP_PRUNE
    except ImportError:
        ENV_SKIP_PRUNE = "AI_HATS_SKIP_RETIRED_PRUNE"

    mp = pytest.MonkeyPatch()
    mp.setenv(ENV_SKIP_PRUNE, "1")
    request.addfinalizer(mp.undo)


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


@pytest.fixture(scope="session", autouse=True)
def _dev_environment_integrity_tripwire():
    """Fail the session loud if any test mutated the developer's python environment (HATS-1164).

    Snapshots ai_hats.__file__, __version__, provider entry points, and src/ pyc count at session start,
    and asserts them unchanged at session end. Prevents test runs from replacing the editable
    dev install with PyPI releases or resurrecting stale entry points.
    """
    import os

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    import importlib.metadata
    from pathlib import Path

    import ai_hats

    src_root = Path(__file__).resolve().parent.parent / "src"

    before_file = getattr(ai_hats, "__file__", None)
    before_ver = getattr(ai_hats, "__version__", None)
    try:
        before_eps = sorted(
            [ep.name for ep in importlib.metadata.entry_points(group="ai_hats.providers")]
        )
    except Exception:
        before_eps = []
    before_pyc = len(list(src_root.glob("**/*.pyc")))

    yield

    after_file = getattr(ai_hats, "__file__", None)
    after_ver = getattr(ai_hats, "__version__", None)
    try:
        after_eps = sorted(
            [ep.name for ep in importlib.metadata.entry_points(group="ai_hats.providers")]
        )
    except Exception:
        after_eps = []
    after_pyc = len(list(src_root.glob("**/*.pyc")))

    deltas = []
    if before_file != after_file:
        deltas.append(f"ai_hats.__file__: {before_file} -> {after_file}")
    if before_ver != after_ver:
        deltas.append(f"version: {before_ver} -> {after_ver}")
    if before_eps != after_eps:
        deltas.append(f"providers entry-points: {before_eps} -> {after_eps}")
    if after_pyc > before_pyc and not os.environ.get("PYTEST_XDIST_WORKER"):
        deltas.append(f"*.pyc count under src/: {before_pyc} -> {after_pyc}")

    if deltas:
        pytest.fail(
            "[dev-env-integrity] a test mutated the developer's environment (HATS-1164):\n  "
            + "\n  ".join(deltas),
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _check_foreign_checkout(request: pytest.FixtureRequest) -> None:
    """Fail the test session loud if ai_hats imports from a foreign checkout (HATS-1242).

    Prevents tests running inside a worktree from silently validating code from
    a different (e.g. main) checkout.
    """
    from pathlib import Path
    import ai_hats
    from tests._checkout_guard import check_checkout_integrity

    rootdir = Path(request.config.rootdir).resolve()
    resolved_init = (
        Path(ai_hats.__file__).resolve()
        if hasattr(ai_hats, "__file__") and ai_hats.__file__
        else None
    )
    try:
        check_checkout_integrity(resolved_init, rootdir)
    except RuntimeError as err:
        pytest.fail(str(err), pytrace=False)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse the session if the composed library layers are foreign (HATS-1429).

    ``_check_foreign_checkout`` proves the *package* is ours; this proves the
    *library* is too — independent axes.

    A hook, not a session fixture, because the damage lands at COLLECTION time:
    ``test_cli_init_flow`` parametrizes its matrix from the library at import, and
    under ``--collect-only`` a fixture never fires at all.
    """
    from pathlib import Path
    from ai_hats.paths.library import builtin_library_layers
    from tests._checkout_guard import check_library_integrity

    rootdir = Path(config.rootdir).resolve()
    try:
        check_library_integrity(builtin_library_layers(), rootdir)
    except RuntimeError as err:
        raise pytest.UsageError(str(err)) from None


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
def _isolate_installed_launcher(tmp_path_factory, monkeypatch):
    """Point the launcher-skew check at nothing for EVERY test (HATS-1617).

    ``_installed_launcher_path`` falls back to ``shutil.which("ai-hats")``, so an
    unpinned test reads the DEVELOPER'S host launcher and the contract-skew
    advisory fires or stays quiet according to whose machine runs the suite.
    Aiming it at a path that does not exist yields *indeterminate* — the silent
    branch. Tests that exercise the advisory set the env themselves (runs after
    this, undone at teardown).
    """
    absent = tmp_path_factory.getbasetemp() / "no-such-launcher"
    monkeypatch.setenv("AI_HATS_LAUNCHER_DEST", str(absent))
    yield


@pytest.fixture(autouse=True)
def _grant_merge_consent(monkeypatch):
    """Grant ``AI_HATS_MERGE_ACK`` — and ONLY that — for every test (HATS-1019).

    ``WorktreeManager.merge`` is default-deny; 43 merge-inventory tests
    (measured on HATS-1682) exercise merge *semantics*, not consent, and all
    red without it. A consent test re-``delenv``s in its own body — this runs
    first, so the local drop wins. Propagates into subprocess e2e by env
    inheritance, which is why the drop has to be local rather than a mark.

    ``AI_HATS_PLAN_ACK`` is deliberately NOT here any more: since HATS-1682 it
    is the env consent channel for ``plan → execute``, so handing it to every
    test made every test of that channel vacuous. Measured blast radius of
    dropping it: one test, which now asks for it explicitly.
    """  # comment-length: allow — the asymmetry between the two flags is the point
    monkeypatch.setenv("AI_HATS_MERGE_ACK", "1")
    # …and the two point-agnostic ones are actively SCRUBBED: a developer whose
    # shell exports either (the documented headless recipe) would otherwise run
    # a suite where nothing about consent can fail.
    for leaked in ("AI_HATS_PLAN_ACK", "AI_HATS_CONSENT_ACK"):
        monkeypatch.delenv(leaked, raising=False)
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
    # HATS-1473: pin, don't unset — unset bottoms out on user_home(), which a
    # subprocess scrubbing AI_HATS_USER_HOME resolves to the developer's real one.
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "_cache_home"))
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
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
