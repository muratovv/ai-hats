"""Tests for the crash-recovery sweep (HATS-648 / R1)."""

from __future__ import annotations

import json
import os
import subprocess
import time

import pytest

from ai_hats import version_recovery, version_refs
from ai_hats.paths import (
    ai_hats_dir,
    complete_sentinel,
    current_pointer,
    version_dir,
    versions_root,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """No AI_HATS_DIR override; trash to a tmp dir so discard never escapes."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))


def _mk_version(project_dir, sha, *, complete, age_hours=0.0):
    """Seed versions/<sha>/ with bin/python; optional sentinel; optional age.
    A real venv always carries the interpreter (bin/python), which
    read_current_sha now requires to resolve `current` (HATS-657). HATS-790
    removed the bin/ai-hats console script, so no proxy binary is seeded —
    usability keys on bin/python + the .complete sentinel."""
    vdir = version_dir(project_dir, sha)
    (vdir / "bin").mkdir(parents=True, exist_ok=True)
    (vdir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    if complete:
        complete_sentinel(project_dir, sha).write_text("", encoding="utf-8")
    if age_hours:
        ts = time.time() - age_hours * 3600
        os.utime(vdir, (ts, ts))  # set LAST so child writes don't bump mtime
    return vdir


def test_sweep_removes_old_incomplete(tmp_path):
    vdir = _mk_version(tmp_path, "deadbeef", complete=False, age_hours=48)
    removed = version_recovery.sweep_incomplete_versions(tmp_path)
    assert removed == [vdir]
    assert not vdir.exists()


def test_sweep_keeps_recent_incomplete(tmp_path):
    """Within the TTL window → may be an install in flight; never removed."""
    vdir = _mk_version(tmp_path, "deadbeef", complete=False, age_hours=0)
    removed = version_recovery.sweep_incomplete_versions(tmp_path)
    assert removed == []
    assert vdir.exists()


def test_sweep_keeps_complete(tmp_path):
    """A complete (sentinel) dir is R2's reclaim, not R1's — kept even when old."""
    vdir = _mk_version(tmp_path, "cafef00d", complete=True, age_hours=48)
    removed = version_recovery.sweep_incomplete_versions(tmp_path)
    assert removed == []
    assert vdir.exists()


def test_sweep_keeps_current_even_if_incomplete(tmp_path):
    """Defensive: the active sha is never swept, regardless of age/state."""
    vdir = _mk_version(tmp_path, "cafef00d", complete=True, age_hours=48)
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    removed = version_recovery.sweep_incomplete_versions(tmp_path)
    assert removed == []
    assert vdir.exists()


def test_sweep_idempotent(tmp_path):
    _mk_version(tmp_path, "deadbeef", complete=False, age_hours=48)
    first = version_recovery.sweep_incomplete_versions(tmp_path)
    second = version_recovery.sweep_incomplete_versions(tmp_path)
    assert len(first) == 1
    assert second == []


def test_sweep_ignores_legacy_venv(tmp_path):
    """The legacy .venv lives OUTSIDE versions/ — the sweep never sees it (the
    broken-versioned → legacy fallback must keep resolving, HATS-649)."""
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "marker").write_text("keep", encoding="utf-8")
    _mk_version(tmp_path, "deadbeef", complete=False, age_hours=48)
    version_recovery.sweep_incomplete_versions(tmp_path)
    assert legacy.exists()
    assert (legacy / "marker").exists()


def test_sweep_skips_pointer_file(tmp_path):
    """The 'current' pointer file under versions/ is not a dir → never touched."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    version_recovery.sweep_incomplete_versions(tmp_path)
    assert current_pointer(tmp_path).exists()


def test_sweep_no_versions_root(tmp_path):
    assert version_recovery.sweep_incomplete_versions(tmp_path) == []


def test_sweep_mixed(tmp_path):
    """Old-incomplete removed; recent-incomplete, complete, current all kept."""
    old_bad = _mk_version(tmp_path, "0ld0bad0", complete=False, age_hours=48)
    new_bad = _mk_version(tmp_path, "neWbad00", complete=False, age_hours=0)
    done = _mk_version(tmp_path, "cafef00d", complete=True, age_hours=48)
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    removed = version_recovery.sweep_incomplete_versions(tmp_path)
    assert removed == [old_bad]
    assert not old_bad.exists()
    assert new_bad.exists() and done.exists()


# ---------- reclaim_orphan_versions (HATS-649 / R2) ----------


def _write_ref(project_dir, sha, *, pid, start_time, name=None):
    """Plant a liveness ref pointing at versions/<sha> for a given pid."""
    d = versions_root(project_dir) / ".refs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name or pid}.json"
    f.write_text(
        json.dumps(
            {"run_id": "test", "root_pid": pid, "start_time": start_time, "sha": sha}
        ),
        encoding="utf-8",
    )
    return f


def _dead_pid() -> int:
    p = subprocess.Popen(["sleep", "30"])
    p.terminate()
    p.wait()
    return p.pid


@pytest.fixture
def live_proc():
    p = subprocess.Popen(["sleep", "60"])
    try:
        yield p
    finally:
        p.kill()
        p.wait()


def _set_current(project_dir, sha):
    """Point `current` at a complete sha so read_current_sha resolves it."""
    current_pointer(project_dir).write_text(f"{sha}\n", encoding="utf-8")


def test_reclaim_removes_complete_non_current_no_ref(tmp_path):
    """Complete + not current + no ref at all → orphaned → reclaimed."""
    cur = _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    orphan = _mk_version(tmp_path, "0ld0c0de", complete=True)
    removed = version_recovery.reclaim_orphan_versions(tmp_path)
    assert removed == [orphan]
    assert not orphan.exists()
    assert cur.exists()  # current untouched


def test_reclaim_removes_dead_ref_and_cleans_ref(tmp_path):
    """A dead ref does not protect its sha, and the ref is swept too."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    orphan = _mk_version(tmp_path, "0ld0c0de", complete=True)
    ref = _write_ref(tmp_path, "0ld0c0de", pid=_dead_pid(), start_time="old")
    removed = version_recovery.reclaim_orphan_versions(tmp_path)
    assert removed == [orphan]
    assert not orphan.exists()
    assert not ref.exists()  # dead ref reclaimed in the same pass


def test_reclaim_keeps_live_ref(tmp_path, live_proc):
    """A live ref (pid alive + start_time match) protects its version."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    pinned = _mk_version(tmp_path, "0ld0c0de", complete=True)
    _write_ref(
        tmp_path,
        "0ld0c0de",
        pid=live_proc.pid,
        start_time=version_refs._proc_start_time(live_proc.pid),
    )
    removed = version_recovery.reclaim_orphan_versions(tmp_path)
    assert removed == []
    assert pinned.exists()


def test_reclaim_keeps_current(tmp_path):
    cur = _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    assert version_recovery.reclaim_orphan_versions(tmp_path) == []
    assert cur.exists()


def test_reclaim_keeps_incomplete(tmp_path):
    """Incomplete dirs are R1's sweep, never reclaimed here (even aged)."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    inc = _mk_version(tmp_path, "badc0de0", complete=False, age_hours=48)
    assert version_recovery.reclaim_orphan_versions(tmp_path) == []
    assert inc.exists()


def test_reclaim_ignores_legacy_venv(tmp_path):
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "marker").write_text("keep", encoding="utf-8")
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    _mk_version(tmp_path, "0ld0c0de", complete=True)
    version_recovery.reclaim_orphan_versions(tmp_path)
    assert legacy.exists() and (legacy / "marker").exists()


def test_reclaim_never_touches_refs_dir(tmp_path, live_proc):
    """The .refs store survives even when it holds only a live ref."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    pinned = _mk_version(tmp_path, "0ld0c0de", complete=True)
    _write_ref(
        tmp_path,
        "0ld0c0de",
        pid=live_proc.pid,
        start_time=version_refs._proc_start_time(live_proc.pid),
    )
    version_recovery.reclaim_orphan_versions(tmp_path)
    assert (versions_root(tmp_path) / ".refs").is_dir()
    assert pinned.exists()


def test_reclaim_dead_ref_to_current_cleans_ref_keeps_current(tmp_path):
    """A dead ref pointing at `current` is cleaned; current is never reclaimed."""
    cur = _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    ref = _write_ref(tmp_path, "cafef00d", pid=_dead_pid(), start_time="old")
    assert version_recovery.reclaim_orphan_versions(tmp_path) == []
    assert cur.exists()
    assert not ref.exists()


def test_reclaim_idempotent(tmp_path):
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    _mk_version(tmp_path, "0ld0c0de", complete=True)
    first = version_recovery.reclaim_orphan_versions(tmp_path)
    second = version_recovery.reclaim_orphan_versions(tmp_path)
    assert len(first) == 1 and second == []


def test_reclaim_keep_shas_protects_target(tmp_path):
    """keep_shas protects a not-yet-current dir (self update's target_sha)."""
    _mk_version(tmp_path, "cafef00d", complete=True)
    _set_current(tmp_path, "cafef00d")
    target = _mk_version(tmp_path, "newtarget", complete=True)
    other = _mk_version(tmp_path, "0ld0c0de", complete=True)
    removed = version_recovery.reclaim_orphan_versions(
        tmp_path, keep_shas={"newtarget"}
    )
    assert removed == [other]
    assert target.exists()
    assert not other.exists()


def test_reclaim_no_versions_root(tmp_path):
    assert version_recovery.reclaim_orphan_versions(tmp_path) == []


# ---- reclaim_legacy_venv (HATS-653 / Phase B) ----


def _mk_legacy_venv(project_dir):
    """Seed a plausible <ai_hats_dir>/.venv with bin/python."""
    legacy = ai_hats_dir(project_dir) / ".venv"
    (legacy / "bin").mkdir(parents=True, exist_ok=True)
    (legacy / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return legacy


def test_reclaim_legacy_venv_removed_when_running_from_versioned(
    tmp_path, monkeypatch
):
    legacy = _mk_legacy_venv(tmp_path)
    monkeypatch.setattr(version_recovery, "current_run_sha", lambda _p: "cafef00d")
    reclaimed = version_recovery.reclaim_legacy_venv(tmp_path)
    assert reclaimed == legacy
    assert not legacy.exists()


def test_reclaim_legacy_venv_kept_when_not_running_from_versioned(
    tmp_path, monkeypatch
):
    """current_run_sha None (running from .venv / override / editable) → keep."""
    legacy = _mk_legacy_venv(tmp_path)
    monkeypatch.setattr(version_recovery, "current_run_sha", lambda _p: None)
    assert version_recovery.reclaim_legacy_venv(tmp_path) is None
    assert legacy.exists()


def test_reclaim_legacy_venv_idempotent(tmp_path, monkeypatch):
    legacy = _mk_legacy_venv(tmp_path)
    monkeypatch.setattr(version_recovery, "current_run_sha", lambda _p: "cafef00d")
    first = version_recovery.reclaim_legacy_venv(tmp_path)
    second = version_recovery.reclaim_legacy_venv(tmp_path)
    assert first == legacy
    assert second is None
    assert not legacy.exists()


def test_reclaim_legacy_venv_missing_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(version_recovery, "current_run_sha", lambda _p: "cafef00d")
    assert version_recovery.reclaim_legacy_venv(tmp_path) is None


def test_reclaim_legacy_venv_never_touches_versions(tmp_path, monkeypatch):
    """The versioned install we run from is left intact; only .venv goes."""
    vdir = _mk_version(tmp_path, "cafef00d", complete=True)
    current_pointer(tmp_path).write_text("cafef00d\n", encoding="utf-8")
    legacy = _mk_legacy_venv(tmp_path)
    monkeypatch.setattr(version_recovery, "current_run_sha", lambda _p: "cafef00d")
    version_recovery.reclaim_legacy_venv(tmp_path)
    assert not legacy.exists()
    assert vdir.is_dir()
    assert current_pointer(tmp_path).read_text().strip() == "cafef00d"
