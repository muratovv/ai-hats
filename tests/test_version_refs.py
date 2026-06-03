"""Tests for liveness refs (HATS-649 / R2).

Cover the four ways a ref's run is classified — live self, dead-via-gone-pid,
dead-via-pid-reuse (start_time mismatch), and the ``ps``-less ``os.kill``
fallback — plus ref write (managed vs legacy) and load (skip malformed).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ai_hats import version_refs
from ai_hats.paths import version_dir, versions_root


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv("AI_HATS_DIR", raising=False)


def _pin(monkeypatch, project_dir, sha):
    """Make this process look like it runs from versions/<sha>/ (set sys.prefix)."""
    vdir = version_dir(project_dir, sha)
    vdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(vdir))
    return vdir


@pytest.fixture
def live_proc():
    """A real, live child process whose pid we can probe; killed on teardown."""
    p = subprocess.Popen(["sleep", "60"])
    try:
        yield p
    finally:
        p.kill()
        p.wait()


def _dead_pid() -> int:
    """A pid that is definitely no longer running (spawned, terminated, reaped)."""
    p = subprocess.Popen(["sleep", "30"])
    p.terminate()
    p.wait()
    return p.pid


# ---------- current_run_sha ----------


def test_current_run_sha_managed(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    assert version_refs.current_run_sha(tmp_path) == "cafef00d"


def test_current_run_sha_legacy_venv_is_none(tmp_path, monkeypatch):
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))
    assert version_refs.current_run_sha(tmp_path) is None


def test_current_run_sha_outside_versions_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "some" / "editable" / "venv"))
    assert version_refs.current_run_sha(tmp_path) is None


def test_current_run_sha_nested_below_sha_is_none(tmp_path, monkeypatch):
    nested = version_dir(tmp_path, "cafef00d") / "bin"
    nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(nested))
    assert version_refs.current_run_sha(tmp_path) is None


# ---------- _proc_start_time ----------


def test_proc_start_time_self_is_nonempty():
    st = version_refs._proc_start_time(os.getpid())
    assert st is not None and st.strip() != ""


def test_proc_start_time_dead_pid_is_none():
    assert version_refs._proc_start_time(_dead_pid()) is None


# ---------- ref_is_live ----------


def test_ref_is_live_self():
    pid = os.getpid()
    ref = {"root_pid": pid, "start_time": version_refs._proc_start_time(pid)}
    assert version_refs.ref_is_live(ref) is True


def test_ref_is_live_reused_pid_mismatch(live_proc):
    # Same live pid, but a start_time that cannot match → pid-reuse → dead.
    ref = {"root_pid": live_proc.pid, "start_time": "Wed Jan  1 00:00:00 2000"}
    assert version_refs.ref_is_live(ref) is False


def test_ref_is_live_gone_pid():
    ref = {"root_pid": _dead_pid(), "start_time": "Wed Jan  1 00:00:00 2000"}
    assert version_refs.ref_is_live(ref) is False


def test_ref_is_live_malformed_is_dead():
    assert version_refs.ref_is_live({}) is False
    assert version_refs.ref_is_live({"root_pid": "notanint"}) is False


def test_ref_is_live_psless_fallback_alive(monkeypatch, live_proc):
    """`ps` unavailable (start_time None) → conservative os.kill liveness."""
    monkeypatch.setattr(version_refs, "_proc_start_time", lambda pid: None)
    ref = {"root_pid": live_proc.pid, "start_time": None}
    assert version_refs.ref_is_live(ref) is True


def test_ref_is_live_psless_fallback_dead(monkeypatch):
    monkeypatch.setattr(version_refs, "_proc_start_time", lambda pid: None)
    ref = {"root_pid": _dead_pid(), "start_time": None}
    assert version_refs.ref_is_live(ref) is False


# ---------- write_current_run_ref ----------


def test_write_ref_managed(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    dest = version_refs.write_current_run_ref(tmp_path)
    assert dest is not None and dest.exists()
    data = json.loads(dest.read_text())
    assert data["root_pid"] == os.getpid()
    assert data["sha"] == "cafef00d"
    assert "run_id" in data
    assert dest.parent == versions_root(tmp_path) / ".refs"
    assert dest.name == f"{os.getpid()}.json"


def test_write_ref_legacy_is_noop(tmp_path, monkeypatch):
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))
    assert version_refs.write_current_run_ref(tmp_path) is None
    assert not (versions_root(tmp_path) / ".refs").exists()


def test_write_ref_idempotent_refresh(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    first = version_refs.write_current_run_ref(tmp_path)
    second = version_refs.write_current_run_ref(tmp_path)
    assert first == second
    refs = list((versions_root(tmp_path) / ".refs").glob("*.json"))
    assert refs == [first]  # one file per process, refreshed not multiplied


# ---------- load_refs ----------


def test_load_refs_skips_malformed_and_hidden(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    good = version_refs.write_current_run_ref(tmp_path)
    refs_dir = versions_root(tmp_path) / ".refs"
    (refs_dir / "broken.json").write_text("{ not json", encoding="utf-8")
    (refs_dir / "notjson.txt").write_text("ignored", encoding="utf-8")
    (refs_dir / ".5.json.tmp").write_text("{}", encoding="utf-8")
    loaded = version_refs.load_refs(tmp_path)
    assert [p for p, _ in loaded] == [good]


def test_load_refs_no_dir(tmp_path):
    assert version_refs.load_refs(tmp_path) == []
