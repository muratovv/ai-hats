"""Tests for the crash-recovery sweep (HATS-648 / R1)."""

from __future__ import annotations

import os
import time

import pytest

from ai_hats import version_recovery
from ai_hats.paths import (
    complete_sentinel,
    current_pointer,
    version_dir,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """No AI_HATS_DIR override; trash to a tmp dir so discard never escapes."""
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", str(tmp_path / "trash"))


def _mk_version(project_dir, sha, *, complete, age_hours=0.0):
    """Seed versions/<sha>/ with bin/ai-hats; optional sentinel; optional age."""
    vdir = version_dir(project_dir, sha)
    (vdir / "bin").mkdir(parents=True, exist_ok=True)
    (vdir / "bin" / "ai-hats").write_text("#!/bin/sh\n", encoding="utf-8")
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
