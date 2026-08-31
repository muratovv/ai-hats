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

import importlib.metadata
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

# HATS-1429: dropped at conftest import, not in the autouse fixture below — modules
# resolve library layers at *collection* time, earlier than any fixture can reach.
# Both halves, never one: HATS-897 scopes AI_HATS_DIR *by* the pin, so dropping the
# pin alone would promote a scoped override into a global one.
for _pinned in ("AI_HATS_PROJECT_DIR", "AI_HATS_DIR"):
    os.environ.pop(_pinned, None)


_EntryPointFingerprint = tuple[tuple[str, int, int], ...]


@dataclass
class _ProviderIntegrityState:
    fingerprint: _EntryPointFingerprint
    surface_names: tuple[str, ...]
    attributed_added: set[str] = field(default_factory=set)
    attributed_removed: set[str] = field(default_factory=set)


def _provider_names() -> tuple[str, ...]:
    return tuple(
        sorted(ep.name for ep in importlib.metadata.entry_points(group="ai_hats.providers"))
    )


def _entry_point_files_fingerprint() -> _EntryPointFingerprint:
    entries: set[tuple[str, int, int]] = set()
    for raw_root in sys.path:
        root = Path(raw_root or ".")
        if not root.is_dir():
            continue
        for entry_points in root.glob("*.dist-info/entry_points.txt"):
            stat = entry_points.stat()
            entries.add((str(entry_points.resolve()), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(entries))


@pytest.fixture(scope="session")
def _provider_integrity_state() -> _ProviderIntegrityState:
    return _ProviderIntegrityState(
        fingerprint=_entry_point_files_fingerprint(),
        surface_names=_provider_names(),
    )


@pytest.fixture(scope="session", autouse=True)
def _workspace_surface_providers() -> Iterator[None]:
    """Register workspace provider classes without installing their distributions."""
    from ai_hats import surface_registry as providers
    from ai_hats.surfaces.agy import AgySurface
    from ai_hats.surfaces.cline import ClineSurface
    from ai_hats.surfaces.codex import CodexSurface

    saved = dict(providers._PROVIDER_REGISTRY)
    for name, provider in (
        ("agy", AgySurface),
        ("cline", ClineSurface),
        ("codex", CodexSurface),
    ):
        if name not in providers._PROVIDER_REGISTRY:
            providers.register_surface(name, provider)
    yield
    providers._PROVIDER_REGISTRY.clear()
    providers._PROVIDER_REGISTRY.update(saved)


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
    """Fail the session loud if the real repo moved while the tests ran (HATS-887).

    Snapshots the checked-out HEAD + this worktree's index at session start and
    asserts them unchanged at session end — catches the "a test wrote real
    ``.git``" class. Deliberately not all-refs: a sibling branch moved by a
    concurrent agent in a shared clone must not trip it. The report states the
    movement and its reflog evidence, never an author (HATS-1675). Watched root
    defaults to this repo; ``AI_HATS_REPO_INTEGRITY_ROOT`` overrides it.
    """
    import os
    from pathlib import Path

    from tests._repo_integrity import describe_movement, snapshot_repo

    override = os.environ.get("AI_HATS_REPO_INTEGRITY_ROOT")
    root = Path(override) if override else Path(__file__).resolve().parent.parent
    before = snapshot_repo(root)
    yield
    if not before.is_repo:
        return
    report = describe_movement(before, snapshot_repo(root), root)
    if report is not None:
        pytest.fail(report, pytrace=False)


@pytest.fixture(scope="session", autouse=True)
def _dev_environment_integrity_tripwire(
    _provider_integrity_state: _ProviderIntegrityState,
):
    """Fail the session loud if any test mutated the developer's python environment (HATS-1164).

    Snapshots ai_hats.__file__, __version__, provider entry points, and src/ pyc count at session start,
    and asserts them unchanged at session end. Prevents test runs from replacing the editable
    dev install with PyPI releases or resurrecting stale entry points.
    """
    import os

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    from pathlib import Path

    import ai_hats

    src_root = Path(__file__).resolve().parent.parent / "src"

    before_file = getattr(ai_hats, "__file__", None)
    before_ver = getattr(ai_hats, "__version__", None)
    before_eps = _provider_integrity_state.surface_names
    before_pyc = len(list(src_root.glob("**/*.pyc")))

    yield

    after_file = getattr(ai_hats, "__file__", None)
    after_ver = getattr(ai_hats, "__version__", None)
    after_eps = _provider_names()
    after_pyc = len(list(src_root.glob("**/*.pyc")))

    deltas = []
    if before_file != after_file:
        deltas.append(f"ai_hats.__file__: {before_file} -> {after_file}")
    if before_ver != after_ver:
        deltas.append(f"version: {before_ver} -> {after_ver}")
    unattributed_added = sorted(
        set(after_eps) - set(before_eps) - _provider_integrity_state.attributed_added
    )
    unattributed_removed = sorted(
        set(before_eps) - set(after_eps) - _provider_integrity_state.attributed_removed
    )
    if unattributed_added or unattributed_removed:
        deltas.append(
            f"providers entry-points: added {unattributed_added}; removed {unattributed_removed}"
        )
    if after_pyc > before_pyc and not os.environ.get("PYTEST_XDIST_WORKER"):
        deltas.append(f"*.pyc count under src/: {before_pyc} -> {after_pyc}")

    if deltas:
        pytest.fail(
            "[dev-env-integrity] the developer's environment changed while the tests "
            "ran (HATS-1164):\n  "
            + "\n  ".join(deltas)
            + "\nA test may have done it; so would a `pip install` in this venv from "
            "another session. This guard observes the change, not its author.",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _provider_entry_point_integrity(
    request: pytest.FixtureRequest,
    _provider_integrity_state: _ProviderIntegrityState,
) -> Iterator[None]:
    """Attribute provider entry-point mutations to their function test."""
    before_fingerprint = _entry_point_files_fingerprint()
    if before_fingerprint == _provider_integrity_state.fingerprint:
        before_names = _provider_integrity_state.surface_names
    else:
        before_names = _provider_names()

    yield

    after_fingerprint = _entry_point_files_fingerprint()
    _provider_integrity_state.fingerprint = after_fingerprint
    if before_fingerprint == after_fingerprint:
        _provider_integrity_state.surface_names = before_names
        return

    after_names = _provider_names()
    _provider_integrity_state.surface_names = after_names
    added = set(after_names) - set(before_names)
    removed = set(before_names) - set(after_names)
    if not added and not removed:
        return

    _provider_integrity_state.attributed_added.update(added)
    _provider_integrity_state.attributed_removed.update(removed)
    pytest.fail(
        f"[provider-entry-point-integrity] {request.node.nodeid} mutated "
        f"ai_hats.providers entry points: {list(before_names)} -> {list(after_names)}",
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


@pytest.fixture(autouse=True, scope="session")
def _codex_base_home_exists(tmp_path_factory):
    """Give the codex surface a base home when the host has none (HATS-1876).

    ``CodexSurface._configured_base_home`` falls back to ``~/.codex`` and raises
    when it is not a directory. A maintainer has one, a CI runner does not, so
    six e2e flows that only touch codex through the surfaces mirror — agy,
    cline and claude among them — died on someone else's surface. Tests that
    drive codex directly set ``CODEX_HOME`` in the child env themselves; this
    fills in the same variable, so theirs still wins.
    """
    configured = os.environ.get("AI_HATS_CODEX_BASE_HOME") or os.environ.get("CODEX_HOME")
    candidate = Path(configured).expanduser() if configured else Path.home() / ".codex"
    if candidate.is_absolute() and candidate.is_dir():
        yield
        return
    home = tmp_path_factory.getbasetemp() / "codex-base-home"
    home.mkdir(exist_ok=True)
    # Session scope, because the e2e launcher fixtures are session-scoped and
    # would otherwise build their venvs and probe the surfaces before a
    # function-scoped fixture had set anything.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CODEX_HOME", str(home))
        yield


@pytest.fixture(autouse=True)
def _consent_wrapper_surfaces_resolve(tmp_path_factory, monkeypatch):
    """Guarantee the wrapped binaries resolve for EVERY test (HATS-1876).

    ``build_consent_wrapper`` raises ``cannot wrap <surface>: executable not
    found on PATH`` when a role declares a consent operation whose surface is
    missing. The surfaces are ``rack`` and ``ai-hats``; ``rack`` is a console
    script, but ``ai-hats`` deliberately is NOT (HATS-790) — it exists only
    where the launcher was installed. So 28 HITL/session tests passed on a
    maintainer's machine and failed in CI, which installs the package and no
    launcher. A stub is planted only for a name PATH cannot already resolve, so
    a host with the real binary is left exactly as it was.
    """
    stubs = tmp_path_factory.getbasetemp() / "consent-surface-stubs"
    stubs.mkdir(exist_ok=True)
    planted = False
    for surface in ("ai-hats", "rack"):
        if shutil.which(surface):
            continue
        stub = stubs / surface
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        planted = True
    if planted:
        monkeypatch.setenv("PATH", f"{stubs}{os.pathsep}{os.environ['PATH']}")
    yield


@pytest.fixture(autouse=True)
def _scrub_legacy_authorization_flags(monkeypatch):
    """Tests opt into wrapper authorization explicitly."""
    for leaked in ("AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK", "AI_HATS_CONSENT_ACK"):
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
