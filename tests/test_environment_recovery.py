"""Tests for the convergent recovery collaborator + its chokepoint wiring (HATS-649 / R2)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

import pytest

from ai_hats.environment_recovery import (
    EnvironmentRecovery,
    NoOpRecovery,
    _LazyLiveness,
    _sweep_orphan_project_keys,
    _sweep_orphan_session_caches,
)
from ai_hats.session_liveness import (
    LivenessSnapshot,
    anchor_path,
    session_owners,
    write_session_anchor,
)
from ai_hats_observe import SessionManager
from ai_hats.paths import (
    cache_home,
    cache_root,
    complete_sentinel,
    current_pointer,
    runs_dir,
    session_cache_dir,
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


# ----- Liveness-gated session-cache reaping (HATS-1339 / S2) -----


def _dead_pid() -> int:
    """A pid that certainly is not running: a child we started and reaped."""
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


class _CountingCapture:
    """Capture seam that records whether a sweep read the process table at all."""

    def __init__(self, snapshot=None):
        self.calls = 0
        self._snapshot = snapshot

    def __call__(self):
        self.calls += 1
        return self._snapshot if self._snapshot is not None else LivenessSnapshot.capture()


@pytest.fixture
def zombie_proc():
    """A child that has exited and is deliberately NOT reaped until teardown.

    The mirror of ``live_proc`` and the state a SIGKILLed wrapper leaves behind
    whenever its own parent is slow to ``wait()``: ``os.kill(pid, 0)`` succeeds
    and ``ps`` still lists the pid with its original ``lstart``, so every cheap
    test for life says yes. Reaping in teardown is what keeps the pid from
    leaking into the rest of the session as a phantom owner.
    """  # comment-length: allow — the fixture IS the defect being reproduced
    proc = subprocess.Popen([sys.executable, "-c", ""])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        # Read the state straight from ``ps`` — asking the code under test
        # whether its own subject exists yet would make the setup circular.
        state = subprocess.run(
            ["ps", "-p", str(proc.pid), "-o", "state="], capture_output=True, text=True
        ).stdout.strip()
        if state.startswith("Z"):
            break
        time.sleep(0.02)
    else:
        pytest.fail(f"pid {proc.pid} never became a zombie; last ps state {state!r}")
    try:
        yield proc
    finally:
        proc.wait()


def _session_dir(project_dir, pid, *, age_hours=0.0, counter=1):
    """A session cache dir named the way ``create_session`` names one."""
    entry = session_cache_root(project_dir) / f"20260101-000000-{counter}-{pid}"
    (entry / "plugin").mkdir(parents=True)
    (entry / "hooks.json").write_text("{}", encoding="utf-8")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(entry, (old, old))
    return entry


def test_live_owner_survives_past_the_ttl(tmp_path):
    """The measured defect: a session's dir mtime is stamped at creation and
    never refreshed, so crossing the TTL deleted 12 running sessions' caches."""
    live = _session_dir(tmp_path, os.getpid(), age_hours=48)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert (live / "hooks.json").exists()
    assert spy.calls == 0, "a running owner must be settled without reading ps"


def test_dead_owner_is_reaped_before_the_ttl(tmp_path):
    """Certain death is reapable NOW — waiting out the TTL is what left 10 of
    these on disk."""
    orphan = _session_dir(tmp_path, _dead_pid())
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert not orphan.exists()
    assert spy.calls == 0, "an absent pid is proof of death without any ps"


def test_a_dir_with_no_baseline_costs_no_process_table_read(tmp_path):
    """Both no-table branches in one sweep: an absent pid is dead on ``os.kill``
    alone, and a living pid with nothing to compare against is alive by
    definition — neither question the table could answer differently."""
    dead = _session_dir(tmp_path, _dead_pid())
    plain = _session_dir(tmp_path, os.getpid(), counter=2)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert not dead.exists()
    assert plain.exists()
    assert spy.calls == 0


def test_recorded_baselines_cost_one_process_table_read_for_the_whole_sweep(tmp_path):
    """The reuse question is the only one that reaches ``ps``, and the snapshot
    it captures is shared by every candidate that asks."""
    first = _session_dir(tmp_path, os.getpid())
    second = _session_dir(tmp_path, os.getpid(), counter=2)
    write_session_anchor(first)
    write_session_anchor(second)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert first.exists() and second.exists(), "our own live baseline must match"
    assert spy.calls == 1


@pytest.fixture
def live_proc():
    """A real, live child process whose pid we can probe; killed on teardown."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def test_reused_pid_reads_as_dead_through_the_lazy_gate(live_proc):
    """Through the INTEGRATED gate, not the snapshot behind it.

    ``os.kill`` cannot tell the owner from whoever inherited its pid, so a living
    pid with a mismatched baseline must still reach the process table — the
    recorded ``start_time`` is the entire reason the anchor exists (plan Q3).
    """
    assert _LazyLiveness().is_live(live_proc.pid, "Mon Jan  1 00:00:00 2001") is False


@pytest.mark.parametrize("baseline", [None, "Mon Jan  1 00:00:00 2001"])
def test_the_cheap_gate_never_answers_differently_from_the_snapshot(
    live_proc, zombie_proc, baseline
):
    """The gate exists to SKIP the table, never to disagree with it.

    Every row of the truth table, cheap path against full snapshot: our own pid,
    a live child, a zombie, and a pid that is certainly gone. Only the
    sub-millisecond exit race between ``os.kill`` and the capture can part them.
    """
    snapshot = LivenessSnapshot.capture()
    for pid in (os.getpid(), live_proc.pid, zombie_proc.pid, _dead_pid()):
        assert _LazyLiveness().is_live(pid, baseline) is snapshot.is_live(pid, baseline)


# ----- The mirror of a dead owner: one that never gets reaped (HATS-1339 D3) -----


@pytest.mark.parametrize("baseline", [None, "Mon Jan  1 00:00:00 2001"])
def test_a_zombie_owner_is_dead_to_both_paths(zombie_proc, baseline):
    """A zombie holds a ``ps`` row and answers ``os.kill`` — and owns nothing.

    Both readings had to change together. The snapshot could not see it (its
    row carries the pid and the original ``lstart``, so it matched the recorded
    baseline exactly), and the cheap gate never asked, so an unreaped wrapper
    pinned its cache for as long as its parent declined to ``wait()`` — where
    the pre-HATS-1339 TTL reclaimed the dir at 24h.
    """  # comment-length: allow — the two readings are the two halves of the fix
    assert _LazyLiveness().is_live(zombie_proc.pid, baseline) is False
    assert LivenessSnapshot.capture().is_live(zombie_proc.pid, baseline) is False


def test_a_zombie_wrapper_costs_no_process_table_read(tmp_path, zombie_proc):
    """And it is settled on the cheap path, so the sweep pays nothing for it."""
    orphan = _session_dir(tmp_path, zombie_proc.pid)
    write_session_anchor(orphan)
    _rewrite_anchor(orphan, root_pid=zombie_proc.pid)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert not orphan.exists(), "an unreaped wrapper pinned its cache dir"
    assert spy.calls == 0


def _rewrite_anchor(entry, **fields) -> None:
    """Overwrite the anchor's fields, keeping the rest of the record."""
    target = anchor_path(entry)
    payload = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    payload.update(fields)
    target.write_text(json.dumps(payload), encoding="utf-8")


# ----- The surface child is the cache's real reader (HATS-1339 D3) -----


def test_a_dir_whose_wrapper_died_but_whose_surface_lives_is_kept(tmp_path, live_proc):
    """The measured defect: the sweep asked only about the WRAPPER.

    On the sub-agent path the surface is spawned over pipes with no controlling
    tty, so a SIGKILL of the wrapper hangs nothing up — a real ``agy`` was still
    running 45s later, reparented to init. Reaping on the wrapper's death alone
    deletes the skills and ``hooks.json`` of a process still reading them.
    """
    entry = _session_dir(tmp_path, _dead_pid())
    write_session_anchor(entry)
    _rewrite_anchor(entry, root_pid=_dead_pid(), start_time_utc=None, child_pid=live_proc.pid)

    _sweep_orphan_session_caches(tmp_path)

    assert entry.exists(), "the cache of a LIVE surface child was reclaimed"
    assert (entry / "hooks.json").exists()


def test_both_owners_gone_reaps_and_names_the_surface_child(tmp_path, caplog):
    """The other half: a recorded child is not a licence to keep the dir."""
    entry = _session_dir(tmp_path, _dead_pid())
    dead_wrapper, dead_child = _dead_pid(), _dead_pid()
    write_session_anchor(entry)
    _rewrite_anchor(entry, root_pid=dead_wrapper, start_time_utc=None, child_pid=dead_child)

    with caplog.at_level(logging.WARNING):
        _sweep_orphan_session_caches(tmp_path)

    assert not entry.exists()
    assert f"owner pid {dead_wrapper} is gone" in caplog.text
    assert f"surface child {dead_child}" in caplog.text


def test_an_anchor_written_before_the_child_field_reads_as_wrapper_only(tmp_path):
    """Every anchor already on disk lacks ``child_pid``; that is not a reader.

    A missing field must mean "nobody else recorded", never "a second owner we
    cannot resolve" — the latter would pin every pre-upgrade dir forever, which
    is the growth this card exists to bound.
    """
    entry = _session_dir(tmp_path, _dead_pid())
    anchor_path(entry).write_text(
        json.dumps({"root_pid": _dead_pid(), "start_time_utc": None}), encoding="utf-8"
    )

    _sweep_orphan_session_caches(tmp_path)

    assert not entry.exists()


def test_a_live_wrapper_never_asks_about_the_child(tmp_path, live_proc):
    """Wrapper first, and ``any()`` stops there — the common case pays nothing."""
    entry = _session_dir(tmp_path, os.getpid())
    write_session_anchor(entry)
    _rewrite_anchor(entry, child_pid=live_proc.pid, child_start_time_utc="Mon Jan  1 00:00:00 2001")
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert entry.exists()
    assert spy.calls == 1, "one memoized capture for our own baseline, none for the child"


def test_a_dir_whose_owner_pid_was_reused_is_reaped(tmp_path, live_proc):
    """The same, end to end through the sweep."""
    entry = _session_dir(tmp_path, live_proc.pid)
    anchor_path(entry).write_text(
        json.dumps({"root_pid": live_proc.pid, "start_time_utc": "Mon Jan  1 00:00:00 2001"}),
        encoding="utf-8",
    )

    _sweep_orphan_session_caches(tmp_path)

    assert not entry.exists()


def test_anchor_overrides_a_reused_dir_name_pid(tmp_path):
    """The dir name says our (live) pid; the anchor says a dead one and wins."""
    entry = _session_dir(tmp_path, os.getpid())
    anchor_path(entry).write_text(
        json.dumps({"root_pid": _dead_pid(), "start_time_utc": None}), encoding="utf-8"
    )

    _sweep_orphan_session_caches(tmp_path)

    assert not entry.exists()


@pytest.mark.parametrize(
    ("aged_name", "fresh_name"),
    [
        ("dry-run-materialize", "dry-run"),
        # A pre-HATS-1248 id ends at the COUNTER, not a pid. Reading that tail as
        # a pid handed the dir to pid 1 (launchd), which never exits, so the dir
        # — and every project key holding one — was retained forever.
        ("20260529-084521-1", "20260530-084521-1"),
        ("sid-1", "sid-2"),
    ],
)
def test_dir_naming_no_owner_still_follows_the_ttl(tmp_path, aged_name, fresh_name):
    """``dry-run-materialize`` and legacy ids carry no pid — age is all there is."""
    root = session_cache_root(tmp_path)
    aged = root / aged_name
    aged.mkdir(parents=True)
    old = time.time() - 48 * 3600
    os.utime(aged, (old, old))
    fresh = root / fresh_name
    fresh.mkdir(parents=True)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert not aged.exists()
    assert fresh.exists()
    assert spy.calls == 0, "an ownerless dir is decided by mtime alone"


def test_reclaim_says_what_it_dropped_and_why(tmp_path, caplog):
    dead = _dead_pid()
    orphan = _session_dir(tmp_path, dead)

    with caplog.at_level("INFO"):
        _sweep_orphan_session_caches(tmp_path)

    assert orphan.name in caplog.text
    assert f"owner pid {dead} is gone" in caplog.text


def test_every_deletion_line_is_loud_enough_to_survive_no_handler(tmp_path, caplog):
    """Nothing in this product configures logging, so a record below WARNING dies
    at ``logging.lastResort``'s threshold and the deletion is never seen. Asserting
    the TEXT alone cannot catch that — a handler installed by the test (or by an
    e2e harness) makes INFO and WARNING read identically.
    """  # comment-length: allow — the level IS the behaviour under test
    _session_dir(tmp_path, _dead_pid())

    with caplog.at_level("INFO"):
        _sweep_orphan_session_caches(tmp_path)

    reclaimed = [r for r in caplog.records if "reclaimed session cache" in r.message]
    assert reclaimed, "the sweep must say what it deleted"
    assert all(r.levelno >= logging.WARNING for r in reclaimed), [
        (r.levelname, r.message) for r in reclaimed
    ]


def test_nothing_to_reap_never_reads_the_process_table(tmp_path):
    """The hot-path contract: an empty cache root costs no ``ps`` (plan Q4)."""
    session_cache_root(tmp_path).mkdir(parents=True)
    spy = _CountingCapture()

    _sweep_orphan_session_caches(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert spy.calls == 0


def test_a_dir_that_vanished_mid_sweep_is_reported(tmp_path, caplog):
    """A peer's normal exit drops its cache dir between the listing and the stat."""
    from ai_hats.environment_recovery import _reap_reason

    gone = session_cache_root(tmp_path) / "legacy-sid"

    with caplog.at_level("WARNING"):
        assert _reap_reason(gone, time.time(), _LazyLiveness()) is None

    assert "session-cache sweep skipped" in caplog.text


def test_unreadable_session_dir_is_reported(tmp_path, caplog):
    """No silent ``except OSError: pass`` survives in the reaping path."""
    entry = _session_dir(tmp_path, os.getpid())
    session_cache_root(tmp_path).chmod(0o000)
    try:
        with caplog.at_level("WARNING"):
            _sweep_orphan_session_caches(tmp_path)
    finally:
        session_cache_root(tmp_path).chmod(0o755)

    assert entry.exists()
    assert "session-cache sweep skipped" in caplog.text


# ----- Foreign keys holding a live session (HATS-1339 / S3) -----


def _foreign_key(name, pid, *, age_days=30):
    key = cache_home() / name
    entry = key / "sessions" / f"20260101-000000-1-{pid}"
    entry.mkdir(parents=True)
    (entry / "hooks.json").write_text("{}", encoding="utf-8")
    for path in (entry, key / "sessions", key):
        _age(path, age_days)
    return key, entry


def test_foreign_key_holding_a_live_session_is_untouched(tmp_path, monkeypatch, caplog):
    """E2: a long session never touches its key's direct children again, so the
    key ages out while the session it holds is still running."""
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    key, entry = _foreign_key("peer-deadbeef", os.getpid())

    with caplog.at_level("INFO"):
        _sweep_orphan_project_keys(tmp_path)

    assert (entry / "hooks.json").exists()
    assert "is running" in caplog.text


def test_stale_key_whose_sessions_are_all_dead_is_reclaimed(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    key, _entry = _foreign_key("gone-deadbeef", _dead_pid())

    _sweep_orphan_project_keys(tmp_path)

    assert not key.exists()


def test_fresh_keys_never_read_the_process_table(tmp_path, monkeypatch):
    """Liveness only ever runs on a key the TTL already condemned."""
    monkeypatch.setenv("AI_HATS_CACHE_HOME", str(tmp_path / "cache"))
    _foreign_key("peer-deadbeef", os.getpid(), age_days=0)
    spy = _CountingCapture()

    _sweep_orphan_project_keys(tmp_path, liveness=_LazyLiveness(capture=spy))

    assert spy.calls == 0


# ----- create_session chokepoint wiring (HATS-1339) -----


def test_recovery_expires_aged_bulk_run_artifacts(tmp_path):
    """``sweep_runs`` rides the same chokepoint — no new command (card Scope §3)."""
    run = runs_dir(tmp_path) / "session_20250101-000000-1-1"
    run.mkdir(parents=True)
    bulk = run / "transcript.jsonl"
    bulk.write_text("{}", encoding="utf-8")
    facts = run / "audit.md"
    facts.write_text("# audit", encoding="utf-8")
    _age(bulk, 90)

    EnvironmentRecovery(tmp_path).run()

    assert not bulk.exists()
    assert facts.exists()


def test_claim_marks_the_cache_dir_with_this_process(tmp_path):
    """The runners' session-start seam; ``session_owners`` reads it back."""
    from ai_hats.runtime_common import _claim_session_cache

    _claim_session_cache(tmp_path, "20260101-000000-1-999999")

    cache_dir = session_cache_dir(tmp_path, "20260101-000000-1-999999")
    ((pid, start_time),) = session_owners(cache_dir)
    assert pid == os.getpid()
    assert start_time, "the claim must record a reuse baseline"


def test_claimed_cache_survives_a_sweep_by_a_peer(tmp_path):
    """End to end for the wiring: claim, age the dir, sweep — it must remain."""
    from ai_hats.runtime_common import _claim_session_cache

    _claim_session_cache(tmp_path, "legacy-sid-without-a-pid")
    cache_dir = session_cache_dir(tmp_path, "legacy-sid-without-a-pid")
    old = time.time() - 48 * 3600
    os.utime(cache_dir, (old, old))

    _sweep_orphan_session_caches(tmp_path)

    assert cache_dir.exists(), "an anchored dir is never decided by age"


def test_a_raising_spawn_callback_never_strands_the_surface(tmp_path, monkeypatch):
    """``on_spawn`` is an injected seam, and a raising one used to leave the child
    running: ``Popen.__exit__`` closes the pipes and then waits, so the runner
    blocked on a surface that can run for hours instead of killing it."""
    from ai_hats.subagent_runner import _run_surface

    def _boom(_pid):
        raise RuntimeError("claim exploded")

    with pytest.raises(RuntimeError, match="claim exploded"):
        _run_surface(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            work_dir=tmp_path,
            env=dict(os.environ),
            timeout_s=30,
            on_spawn=_boom,
        )
