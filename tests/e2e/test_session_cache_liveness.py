"""e2e (HATS-1339)

flow:   a maintainer keeps one long session open past the cache TTL, a peer
        session crashes, and a third session starts with both caches on disk
cmds:
    ai-hats -r maintainer
expect: the crashed session's cache is reclaimed at once and named in the log,
        while the live session's plugin skills and settings.json survive
why:    age alone reaped 12 live maintainer sessions' skills and hooks mid-flight
"""

# Three real ``ai-hats`` processes, no in-process shortcut: the defect exists
# only between processes — one run's sweep deleting another run's cache — and
# the liveness gate resolves owners through a real ``ps``. The surface they
# launch is ``_helpers.fake_surface``, which also explains how the sweeps' log
# lines are made observable at all.
# comment-length: allow — names why this tier is the only one that can prove it

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest
from ai_hats.environment_recovery import PROJECT_KEY_TTL_DAYS, SESSION_CACHE_TTL_HOURS
from ai_hats.session_liveness import ANCHOR_NAME

from _helpers.fake_surface import install

pytestmark = pytest.mark.integration


@pytest.fixture
def two_sessions(tmp_project, tmp_path: Path, repo_root: Path):
    """The card's setup: two live sessions, both fully materialized."""
    surface = install(tmp_project, tmp_path, repo_root)
    live = surface.start_held("live")
    doomed = surface.start_held("doomed")
    yield surface, live, doomed
    live.kill_if_running()
    doomed.kill_if_running()


def _backdate(path: Path, *, hours: float) -> None:
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


def test_the_third_run_reaps_only_the_killed_sessions_cache(two_sessions) -> None:
    """The card's acceptance scenario, and the two properties it turns on.

    The live session's dir is backdated past the TTL because that IS the defect:
    a cache dir's mtime is stamped at creation and never moves, so the 12
    maintainer sessions that lost their skills mid-flight were simply sessions
    that outlived 24 hours. The killed session's dir is seconds old — reaping it
    proves the sweep no longer waits the TTL out.
    """  # comment-length: allow — one paragraph per property under test
    surface, live, doomed = two_sessions
    settings_before = (live.cache_dir / "settings.json").read_bytes()
    skills = live.cache_dir / "plugin" / "skills"
    skills_before = sorted(p.name for p in skills.iterdir())
    assert skills_before, "precondition: the live session materialized no skills to lose"

    _backdate(live.cache_dir, hours=SESSION_CACHE_TTL_HOURS * 2)
    doomed.kill_and_reap()
    doomed_age_h = (time.time() - doomed.cache_dir.stat().st_mtime) / 3600

    done = surface.run_once()

    assert not doomed.cache_dir.exists(), (
        f"the killed session's cache survived the next run: {doomed.cache_dir}"
    )
    assert doomed_age_h < SESSION_CACHE_TTL_HOURS, (
        f"the reap cannot be attributed to death: the dir was already "
        f"{doomed_age_h:.2f}h old against a {SESSION_CACHE_TTL_HOURS}h TTL"
    )
    assert live.cache_dir.is_dir(), (
        f"a RUNNING session lost its cache dir to the sweep: {live.cache_dir}"
    )
    assert (live.cache_dir / "settings.json").read_bytes() == settings_before, (
        "the live session's hooks manifest was rewritten or truncated under it"
    )
    assert sorted(p.name for p in skills.iterdir()) == skills_before, (
        "the live session's plugin-skills mirror was reaped mid-flight — the "
        "exact harm HATS-1339 measured 12 times"
    )
    assert f"reclaimed session cache {doomed.sid}" in done.stderr, (
        f"the reclaim was silent; stderr:\n{done.stderr[-2000:]}"
    )
    assert f"owner pid {doomed.pid} is gone" in done.stderr, (
        f"the log line names no reason; stderr:\n{done.stderr[-2000:]}"
    )


def _pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _await_pid_gone(pid: int, *, timeout_s: float) -> float | None:
    """Seconds until ``pid`` disappeared, or ``None`` if it outlived the wait."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        if _pid_gone(pid):
            return time.monotonic() - t0
        time.sleep(0.1)
    return None


#: Measured on macOS 15: a real ``claude`` is gone ~1s after its wrapper's
#: SIGKILL, ``agy``'s two-level pty chain within 5-10s. The bound is the window
#: in which a reaped cache could still have a reader, not a timing preference.
ORPHAN_GRACE_S = 20.0


def test_a_killed_wrappers_surface_child_goes_with_it(two_sessions) -> None:
    """Reaping a dead owner's cache cannot strand a live reader (HATS-1339 D3).

    The sweep asks whether the WRAPPER is alive, but the process that reads
    ``plugin/skills`` and ``settings.json`` out of the dir is the surface CLI
    below it. That gap is only harmless because ``_pty_spawn`` gives the surface
    a pty whose master nobody but the wrapper holds: the wrapper dies, the
    kernel drops carrier, and the session leader on the other end is hung up.

    Fail-under-revert: spawn the surface over pipes instead of a pty, or leave a
    second holder of the master fd open, and the child outlives its owner — the
    sweep then deletes the skills of a process still reading them, with none of
    the 24h the TTL used to buy. The kill is one pid on purpose: the surface is
    a session leader of its own, so ``killpg`` never described what happens.
    """  # comment-length: allow — the guarantee is the whole point of the test
    _surface, _live, doomed = two_sessions
    surface_pid = doomed.surface_pid
    assert not _pid_gone(surface_pid), (
        "precondition: the surface child was gone before the wrapper was killed"
    )

    doomed.kill_and_reap()

    elapsed = _await_pid_gone(surface_pid, timeout_s=ORPHAN_GRACE_S)
    assert elapsed is not None, (
        f"the surface child (pid {surface_pid}) outlived its SIGKILLed wrapper "
        f"(pid {doomed.pid}) by more than {ORPHAN_GRACE_S:.0f}s — the next run's "
        f"sweep will reap {doomed.cache_dir} out from under a live reader"
    )


@pytest.fixture
def orphaned_subagent(tmp_project, tmp_path: Path, repo_root: Path):
    """A sub-agent whose wrapper is SIGKILLed while its surface keeps running.

    The AUTOMATE path spawns the surface over pipes with no controlling tty, so
    unlike the HITL path nothing hangs it up — this stages the orphan that a
    real ``agy`` was measured to become (alive 45s on, reparented to init),
    rather than simulating one.
    """
    surface = install(tmp_project, tmp_path, repo_root)
    session = surface.start_held("subagent", automate=True)
    child = session.surface_pid
    session.kill_and_reap()
    yield surface, session, child
    if not _pid_gone(child):
        os.kill(child, signal.SIGKILL)


def test_a_dead_wrappers_cache_is_kept_while_its_surface_still_reads_it(
    orphaned_subagent,
) -> None:
    """The wrapper owns the dir; the surface is what READS it (HATS-1339 D3).

    Fail-under-revert: ask only about ``root_pid`` and the very next run deletes
    this dir — the skills mirror and ``settings.json`` of a process still
    reading them, with none of the 24h the pre-HATS-1339 TTL bought. The kill is
    one pid, never ``killpg``, precisely so the surface survives it.
    """
    surface, session, child = orphaned_subagent
    assert not _pid_gone(child), (
        "precondition: the surface child died with its wrapper, so there is no "
        "orphan here to strand — the AUTOMATE path grew a teardown it never had"
    )
    anchor = json.loads((session.cache_dir / ANCHOR_NAME).read_text())
    assert anchor.get("child_pid") == child, f"the anchor never learned the surface pid: {anchor}"

    done = surface.run_once()

    assert session.cache_dir.is_dir(), (
        f"a RUNNING surface child lost its cache to a peer's sweep: {session.cache_dir}"
    )
    assert (session.cache_dir / "settings.json").is_file(), "its hooks manifest went with it"
    assert f"reclaimed session cache {session.sid}" not in done.stderr


def test_and_that_cache_is_reclaimed_once_the_surface_is_gone_too(orphaned_subagent) -> None:
    """The other half — a recorded child must not pin the dir forever."""
    surface, session, child = orphaned_subagent
    os.kill(child, signal.SIGKILL)
    assert _await_pid_gone(child, timeout_s=ORPHAN_GRACE_S) is not None

    done = surface.run_once()

    assert not session.cache_dir.exists(), (
        f"both owners are gone and the cache stayed: {session.cache_dir}"
    )
    assert f"surface child {child}" in done.stderr, (
        f"the reclaim did not name the second owner; stderr:\n{done.stderr[-2000:]}"
    )


def _plant_foreign_key(cache_home: Path, name: str, sid: str, anchor: bytes) -> Path:
    """A sibling project's cache key, untouched past its TTL, holding one session.

    Only the key and its DIRECT children carry the age the sweep reads
    (``_key_last_touched``) — which is why a long session's key looks abandoned:
    nothing touches it again once the session is under way.
    """
    key = cache_home / name
    session = key / "sessions" / sid
    session.mkdir(parents=True)
    (session / ANCHOR_NAME).write_bytes(anchor)
    stale = time.time() - (PROJECT_KEY_TTL_DAYS + 1) * 86400
    for path in (key / "sessions", key):
        os.utime(path, (stale, stale))
    return key


def test_a_foreign_key_holding_a_live_session_is_left_alone(two_sessions) -> None:
    """E2: the key sweep runs across projects, so it reaches sessions it did not
    start — and a stale key is exactly what a long-running session leaves.

    The dead-owner key beside it is the control: without it, "the live key
    survived" would also pass a sweep that did nothing at all.
    """
    surface, live, doomed = two_sessions
    live_anchor = (live.cache_dir / ANCHOR_NAME).read_bytes()
    dead_anchor = (doomed.cache_dir / ANCHOR_NAME).read_bytes()
    doomed.kill_and_reap()

    live_sid = f"20240101-000000-1-{live.pid}"
    dead_sid = f"20240101-000000-1-{doomed.pid}"
    kept = _plant_foreign_key(surface.cache_home, "peer-live-0badcafe", live_sid, live_anchor)
    orphan = _plant_foreign_key(surface.cache_home, "peer-dead-0badcafe", dead_sid, dead_anchor)

    done = surface.run_once()

    assert kept.is_dir(), (
        "a foreign cache key holding a RUNNING session was reclaimed by another "
        f"project's sweep: {kept}"
    )
    assert (kept / "sessions" / live_sid).is_dir(), "the live session's dir went with the key"
    assert not orphan.exists(), (
        f"the control key (same age, dead owner) survived — the sweep did not run: {orphan}"
    )
    assert f"project cache key {kept.name} kept: session {live_sid} is running" in done.stderr, (
        f"keeping the key was silent; stderr:\n{done.stderr[-2000:]}"
    )
    assert f"reclaimed orphaned project cache key: {orphan.name}" in done.stderr, (
        f"the reclaim was silent; stderr:\n{done.stderr[-2000:]}"
    )
