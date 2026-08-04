"""Tests for the convergent recovery collaborator + its chokepoint wiring (HATS-649 / R2)."""

from __future__ import annotations

import os
import sys
import time

import pytest

from ai_hats.environment_recovery import (
    EnvironmentRecovery,
    NoOpRecovery,
    _sweep_orphan_project_keys,
    _sweep_orphan_session_caches,
)
from ai_hats_observe import SessionManager
from ai_hats.paths import (
    cache_home,
    cache_root,
    complete_sentinel,
    current_pointer,
    runs_dir,
    session_cache_root,
    version_dir,
    versions_root,
)
from ai_hats.paths import ENV_AI_HATS_DIR


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))


def _mk_complete_version(project_dir, sha):
    vdir = version_dir(project_dir, sha)
    (vdir / "bin").mkdir(parents=True, exist_ok=True)
    (vdir / "bin" / "ai-hats").write_text("#!/bin/sh\n", encoding="utf-8")
    # bin/python — read_current_sha requires a runnable interpreter (HATS-657),
    # so a "complete version" the reclaim tests treat as `current` must have it.
    (vdir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    complete_sentinel(project_dir, sha).write_text("", encoding="utf-8")
    return vdir


# ---------- ordering: own ref written before reclaim ----------


def test_run_protects_own_non_current_pin(tmp_path, monkeypatch):
    """A run pinned to a now-non-current sha must NOT reclaim its own version:
    EnvironmentRecovery writes our ref before the reclaim pass observes it."""
    _mk_complete_version(tmp_path, "neWc0de0")
    current_pointer(tmp_path).write_text("neWc0de0\n", encoding="utf-8")
    pinned = _mk_complete_version(tmp_path, "0ldc0de0")  # what WE run from
    monkeypatch.setattr(sys, "prefix", str(pinned))

    EnvironmentRecovery(tmp_path).run()

    assert pinned.exists()  # our live ref (written first) protected it
    ref = versions_root(tmp_path) / ".refs" / f"{os.getpid()}.json"
    assert ref.exists()


def test_run_reclaims_unpinned_orphan(tmp_path, monkeypatch):
    """Same layout, but this process does NOT run from the orphan → reclaimed."""
    _mk_complete_version(tmp_path, "neWc0de0")
    current_pointer(tmp_path).write_text("neWc0de0\n", encoding="utf-8")
    orphan = _mk_complete_version(tmp_path, "0ldc0de0")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))  # legacy run pins nothing

    EnvironmentRecovery(tmp_path).run()

    assert not orphan.exists()


# ---------- legacy .venv reclaim wiring (HATS-653 / Phase B) ----------


def test_run_reclaims_legacy_venv_when_running_from_versioned(tmp_path, monkeypatch):
    """We run from a complete versioned venv → the orphaned .venv is reclaimed."""
    pinned = _mk_complete_version(tmp_path, "cafef00d")
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    (legacy / "bin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(pinned))

    EnvironmentRecovery(tmp_path).run()

    assert not legacy.exists()
    assert pinned.is_dir()  # the versioned venv we run from is untouched


def test_run_keeps_legacy_venv_on_legacy_run(tmp_path, monkeypatch):
    """A run from .venv itself (current_run_sha None) must keep .venv."""
    _mk_complete_version(tmp_path, "cafef00d")
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    (legacy / "bin").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))  # running from .venv

    EnvironmentRecovery(tmp_path).run()

    assert legacy.exists()


# ---------- HATS-650 / R3: GC under the crash-safe lock ----------


def test_run_skips_gc_when_version_lock_held(tmp_path, monkeypatch):
    """Hot-path GC is opportunistic: a held version lock → GC is skipped, never
    raises, and the orphan survives this pass (the next invocation converges)."""
    import filelock

    from ai_hats.version_lock import gc_lock_path

    _mk_complete_version(tmp_path, "neWc0de0")
    current_pointer(tmp_path).write_text("neWc0de0\n", encoding="utf-8")
    orphan = _mk_complete_version(tmp_path, "0ldc0de0")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))  # this run pins nothing
    # Keep the test fast: a tiny timeout still proves the swallow path.
    monkeypatch.setattr("ai_hats.environment_recovery.GC_LOCK_TIMEOUT", 0.2)

    gc_lock_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    holder = filelock.FileLock(str(gc_lock_path(tmp_path)), timeout=1.0)
    holder.acquire()
    try:
        EnvironmentRecovery(tmp_path).run()  # must not raise
    finally:
        holder.release()

    assert orphan.exists(), "GC reclaimed under a held lock (should have skipped)"


def test_run_swallows_oserror_in_version_gc(tmp_path, monkeypatch):
    """An I/O error mid-version-GC is swallowed (never breaks create_session);
    the later legacy-.venv reclaim still runs."""
    _mk_complete_version(tmp_path, "cafef00d")
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))  # this run pins nothing

    def _boom(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr("ai_hats.environment_recovery.reclaim_orphan_versions", _boom)

    EnvironmentRecovery(tmp_path).run()  # must not raise


# ---------- moved session-cache sweep still works ----------


def test_sweep_orphan_session_caches_moved(tmp_path):
    root = session_cache_root(tmp_path)
    aged = root / "old-sid"
    aged.mkdir(parents=True)
    old = time.time() - 48 * 3600
    os.utime(aged, (old, old))
    recent = root / "new-sid"
    recent.mkdir(parents=True)

    _sweep_orphan_session_caches(tmp_path)

    assert not aged.exists()
    assert recent.exists()


@pytest.fixture
def legacy_cache(tmp_path, monkeypatch):
    """The pre-HATS-1398 in-tree cache dir of a project."""
    monkeypatch.setenv("AI_HATS_DIR", str(tmp_path / ".agent" / "ai-hats"))
    monkeypatch.setenv("AI_HATS_PROJECT_DIR", str(tmp_path))
    legacy = tmp_path / ".agent" / "ai-hats" / ".cache"
    legacy.mkdir(parents=True)
    return legacy


def test_sweep_drops_the_whole_in_tree_cache(tmp_path, legacy_cache):
    """The cache is regenerable, so the old root is dropped — not migrated."""
    mirror = legacy_cache / "probe-mirror"
    mirror.mkdir()
    (mirror / "HEAD").write_text("ref: refs/heads/master\n")
    (legacy_cache / "update-check.json").write_text("{}")
    aged = legacy_cache / "sessions" / "old-sid"
    aged.mkdir(parents=True)
    old = time.time() - 48 * 3600
    os.utime(aged, (old, old))

    _sweep_orphan_session_caches(tmp_path)

    assert not legacy_cache.exists(), "no cache may survive inside the workspace"


def test_sweep_spares_an_in_tree_session_dir_that_may_still_be_live(tmp_path, legacy_cache):
    """A recent pre-move dir may belong to a session still reading it."""
    live = legacy_cache / "sessions" / "live-sid"
    live.mkdir(parents=True)

    _sweep_orphan_session_caches(tmp_path)

    assert live.exists()


# ---------- SessionManager DI ----------


class _SpyRecovery:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1


def test_session_manager_calls_recovery_once_per_create(tmp_path):
    spy = _SpyRecovery()
    mgr = SessionManager(tmp_path, runs_dir=runs_dir(tmp_path), recovery=spy)
    mgr.create_session()
    mgr.create_session()
    assert spy.calls == 2


def test_session_manager_noop_recovery_no_fs_effects(tmp_path, monkeypatch):
    """NoOpRecovery → create_session works and writes no liveness ref."""
    pinned = _mk_complete_version(tmp_path, "cafef00d")
    monkeypatch.setattr(sys, "prefix", str(pinned))
    mgr = SessionManager(tmp_path, runs_dir=runs_dir(tmp_path), recovery=NoOpRecovery())
    session = mgr.create_session()
    assert session.session_id
    assert not (versions_root(tmp_path) / ".refs").exists()


def test_run_path_seam_runs_real_recovery(tmp_path, monkeypatch):
    """The run-path seam injects real recovery: a pinned process writes a ref.

    HATS-948: observe's default is now a no-op (package-pure); the version-GC
    recovery is wired by ``make_session_manager``, not by the bare constructor.
    """
    from ai_hats.composition_seam import make_session_manager

    pinned = _mk_complete_version(tmp_path, "cafef00d")
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(pinned))
    make_session_manager(tmp_path).create_session()
    assert (versions_root(tmp_path) / ".refs" / f"{os.getpid()}.json").exists()


def test_session_manager_default_recovery_is_noop(tmp_path, monkeypatch):
    """HATS-948: the bare default no longer touches the version subsystem."""
    pinned = _mk_complete_version(tmp_path, "cafef00d")
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(pinned))
    SessionManager(tmp_path, runs_dir=runs_dir(tmp_path)).create_session()
    assert not (versions_root(tmp_path) / ".refs" / f"{os.getpid()}.json").exists()


# ----- Cross-project key sweep (HATS-1473) -----


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_key_older_than_ttl_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    stale = cache_home() / "gone-deadbeef"
    (stale / "sessions").mkdir(parents=True)
    _age(stale / "sessions", 30)
    _age(stale, 30)

    _sweep_orphan_project_keys(tmp_path)

    assert not stale.exists()


def test_fresh_key_and_own_key_survive(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    fresh = cache_home() / "fresh-deadbeef"
    (fresh / "sessions").mkdir(parents=True)
    own = cache_root(tmp_path)
    (own / "sessions").mkdir(parents=True)
    _age(own / "sessions", 30)
    _age(own, 30)

    _sweep_orphan_project_keys(tmp_path)

    assert fresh.exists()
    assert own.exists(), "the current project's own key must never be swept"


def test_deep_write_keeps_key_alive(tmp_path, monkeypatch):
    """A key whose own mtime is ancient but whose sessions/ is fresh is LIVE.

    A directory's mtime only moves when a direct child changes, so ageing the key
    dir alone is exactly the shape a long-lived project has.
    """
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    live = cache_home() / "live-deadbeef"
    (live / "sessions" / "sid-1").mkdir(parents=True)
    _age(live, 30)

    _sweep_orphan_project_keys(tmp_path)

    assert live.exists(), "a fresh direct child must protect the key"


def test_missing_cache_home_is_a_quiet_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "never-created"))

    _sweep_orphan_project_keys(tmp_path)  # must not raise


def test_unreadable_cache_home_is_reported(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    cache_home().mkdir(parents=True)
    cache_home().chmod(0o000)
    try:
        with caplog.at_level("WARNING"):
            _sweep_orphan_project_keys(tmp_path)
    finally:
        cache_home().chmod(0o755)

    assert "cache home unreadable" in caplog.text
