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

import os
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
