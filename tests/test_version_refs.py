"""Tests for liveness refs (HATS-649 / R2).

Cover the four ways a ref's run is classified — live self, dead-via-gone-pid,
dead-via-pid-reuse (start_time mismatch), and the ``ps``-less ``os.kill``
fallback — plus ref write (managed vs legacy) and load (skip malformed).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import os
import subprocess
import sys

import pytest

from ai_hats import version_refs
from ai_hats.paths import ENV_AI_HATS_DIR


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)


def _pin(monkeypatch, project_dir, sha):
    """Make this process look like it runs from versions/<sha>/ (set sys.prefix)."""
    vdir = ProjectLayout.at(project_dir).versions.dir(sha)
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
    assert version_refs.current_run_sha(ProjectLayout.at(tmp_path).versions) == "cafef00d"


def test_current_run_sha_legacy_venv_is_none(tmp_path, monkeypatch):
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))
    assert version_refs.current_run_sha(ProjectLayout.at(tmp_path).versions) is None


def test_current_run_sha_outside_versions_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "some" / "editable" / "venv"))
    assert version_refs.current_run_sha(ProjectLayout.at(tmp_path).versions) is None


def test_current_run_sha_nested_below_sha_is_none(tmp_path, monkeypatch):
    nested = ProjectLayout.at(tmp_path).versions.dir("cafef00d") / "bin"
    nested.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(nested))
    assert version_refs.current_run_sha(ProjectLayout.at(tmp_path).versions) is None


# ---------- _proc_start_time ----------


def test_proc_start_time_self_is_nonempty():
    st = version_refs._proc_start_time(os.getpid())
    assert st is not None and st.strip() != ""


def test_proc_start_time_dead_pid_is_none():
    assert version_refs._proc_start_time(_dead_pid()) is None


# ---------- ref_is_live ----------


def test_ref_is_live_self():
    pid = os.getpid()
    ref = {"root_pid": pid, "start_time_utc": version_refs._proc_start_time(pid)}
    assert version_refs.ref_is_live(ref) is True


def test_ref_is_live_reused_pid_mismatch(live_proc):
    # Same live pid, but a start_time that cannot match → pid-reuse → dead.
    ref = {"root_pid": live_proc.pid, "start_time_utc": "Wed Jan  1 00:00:00 2000"}
    assert version_refs.ref_is_live(ref) is False


def test_ref_is_live_gone_pid():
    ref = {"root_pid": _dead_pid(), "start_time_utc": "Wed Jan  1 00:00:00 2000"}
    assert version_refs.ref_is_live(ref) is False


def test_the_baseline_ignores_the_ambient_timezone(monkeypatch, live_proc):
    """One run writes the ref and ANOTHER checks it. `ps -o lstart=` renders in
    the TZ/locale of the ps process, so unpinned the two disagreed and a LIVE run
    read as a reused pid — reclaiming the version it is executing from."""
    monkeypatch.setenv("TZ", "America/New_York")
    written = version_refs._proc_start_time(live_proc.pid)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert version_refs._proc_start_time(live_proc.pid) == written
    assert version_refs.ref_is_live({"root_pid": live_proc.pid, "start_time_utc": written}) is True


def test_a_legacy_start_time_is_not_read_as_a_baseline(live_proc):
    """Refs already on disk were rendered in an unknown locale; reading one would
    reclaim a live run's version. Absent baseline → the os.kill fallback keeps it."""
    ref = {"root_pid": live_proc.pid, "start_time": "Wed Jan  1 00:00:00 2000"}
    assert version_refs.ref_is_live(ref) is True


def test_ref_is_live_malformed_is_dead():
    assert version_refs.ref_is_live({}) is False
    assert version_refs.ref_is_live({"root_pid": "notanint"}) is False


def test_ref_is_live_psless_fallback_alive(monkeypatch, live_proc):
    """`ps` unavailable (start_time None) → conservative os.kill liveness."""
    monkeypatch.setattr(version_refs, "_proc_start_time", lambda pid: None)
    ref = {"root_pid": live_proc.pid, "start_time_utc": None}
    assert version_refs.ref_is_live(ref) is True


def test_ref_is_live_psless_fallback_dead(monkeypatch):
    monkeypatch.setattr(version_refs, "_proc_start_time", lambda pid: None)
    ref = {"root_pid": _dead_pid(), "start_time_utc": None}
    assert version_refs.ref_is_live(ref) is False


# ---------- write_current_run_ref ----------


def test_write_ref_managed(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    dest = version_refs.write_current_run_ref(ProjectLayout.at(tmp_path).versions)
    assert dest is not None and dest.exists()
    data = json.loads(dest.read_text())
    assert data["root_pid"] == os.getpid()
    assert data["sha"] == "cafef00d"
    assert "run_id" in data
    assert dest.parent == ProjectLayout.at(tmp_path).versions.root / ".refs"
    assert dest.name == f"{os.getpid()}.json"


def test_write_ref_legacy_is_noop(tmp_path, monkeypatch):
    legacy = tmp_path / ".agent" / "ai-hats" / ".venv"
    legacy.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(legacy))
    assert version_refs.write_current_run_ref(ProjectLayout.at(tmp_path).versions) is None
    assert not (ProjectLayout.at(tmp_path).versions.root / ".refs").exists()


def test_write_ref_idempotent_refresh(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    first = version_refs.write_current_run_ref(ProjectLayout.at(tmp_path).versions)
    second = version_refs.write_current_run_ref(ProjectLayout.at(tmp_path).versions)
    assert first == second
    refs = list((ProjectLayout.at(tmp_path).versions.root / ".refs").glob("*.json"))
    assert refs == [first]  # one file per process, refreshed not multiplied


# ---------- load_refs ----------


def test_load_refs_skips_malformed_and_hidden(tmp_path, monkeypatch):
    _pin(monkeypatch, tmp_path, "cafef00d")
    good = version_refs.write_current_run_ref(ProjectLayout.at(tmp_path).versions)
    refs_dir = ProjectLayout.at(tmp_path).versions.root / ".refs"
    (refs_dir / "broken.json").write_text("{ not json", encoding="utf-8")
    (refs_dir / "notjson.txt").write_text("ignored", encoding="utf-8")
    (refs_dir / ".5.json.tmp").write_text("{}", encoding="utf-8")
    loaded = version_refs.load_refs(ProjectLayout.at(tmp_path).versions)
    assert [p for p, _ in loaded] == [good]


def test_load_refs_no_dir(tmp_path):
    assert version_refs.load_refs(ProjectLayout.at(tmp_path).versions) == []


# ---------- the predicates: complete / usable / current (HATS-647/648/657/790) ----------


def _seed_version(
    project_dir, sha, *, make_dir=True, complete=True, pointer=True, sentinel=None, python=None
):
    """Seed versions/<sha>/ (a fake venv) and/or versions/current.

    Usability is ``.complete`` sentinel + ``bin/python`` (the launcher execs
    ``python -m ai_hats``; there is no console script). ``complete`` seeds both;
    ``python`` and ``sentinel`` override each axis so a test can seed crash
    residue (python, no sentinel), a corrupted-after-complete venv (sentinel, no
    python) or a python-broken one. read_current_sha requires BOTH.
    """
    versions = ProjectLayout.at(project_dir).versions
    versions.root.mkdir(parents=True, exist_ok=True)
    write_sentinel = complete if sentinel is None else sentinel
    write_python = complete if python is None else python
    if make_dir:
        vdir = versions.dir(sha)
        vdir.mkdir(parents=True, exist_ok=True)
        if write_python:
            (vdir / "bin").mkdir(parents=True, exist_ok=True)
            (vdir / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        if write_sentinel:
            versions.sentinel(sha).write_text("", encoding="utf-8")
    if pointer:
        versions.current_pointer.write_text(f"{sha}\n", encoding="utf-8")
    return versions


def test_read_current_sha_present(tmp_path):
    """Pointer present + a usable versions/<sha>/ → the sha."""
    versions = _seed_version(tmp_path, "deadbeef")
    assert version_refs.read_current_sha(versions) == "deadbeef"


def test_read_current_sha_missing_pointer(tmp_path):
    """No pointer at all → None (legacy install, not yet versioned)."""
    assert version_refs.read_current_sha(ProjectLayout.at(tmp_path).versions) is None


def test_read_current_sha_dangling(tmp_path):
    """Pointer present but versions/<sha>/ absent → None."""
    versions = _seed_version(tmp_path, "deadbeef", make_dir=False)
    assert version_refs.read_current_sha(versions) is None


def test_read_current_sha_corrupt_venv(tmp_path):
    """Sentinel present but bin/python gone → None, so callers degrade to the
    self-healing default venv; the corruption guard is distinct from the
    incompleteness gate."""
    versions = _seed_version(tmp_path, "deadbeef", complete=False, sentinel=True)
    assert version_refs.read_current_sha(versions) is None


def test_read_current_sha_no_sentinel(tmp_path):
    """bin/python present but no .complete (install killed mid-pip) → None: the
    sentinel is the completeness authority."""
    versions = _seed_version(tmp_path, "deadbeef", complete=True, sentinel=False)
    assert version_refs.read_current_sha(versions) is None


def test_read_current_sha_broken_python(tmp_path):
    """Complete but bin/python gone (a host python upgrade dangles the symlink) →
    None: complete is not runnable, so self update must rebuild it."""
    versions = _seed_version(tmp_path, "deadbeef", complete=True, sentinel=True, python=False)
    assert version_refs.read_current_sha(versions) is None


def test_is_usable_version_requires_python(tmp_path):
    """is_usable_version is True only with sentinel AND bin/python — stronger
    than is_complete (sentinel only)."""
    versions = _seed_version(tmp_path, "deadbeef", complete=True, sentinel=True, pointer=False)
    assert version_refs.is_complete(versions, "deadbeef") is True
    assert version_refs.is_usable_version(versions, "deadbeef") is True
    (versions.dir("deadbeef") / "bin" / "python").unlink()
    assert version_refs.is_complete(versions, "deadbeef") is True
    assert version_refs.is_usable_version(versions, "deadbeef") is False


def test_is_complete_gates_on_sentinel(tmp_path):
    """is_complete is True iff the .complete sentinel is present, whatever bin/python says."""
    versions = _seed_version(tmp_path, "deadbeef", complete=True, sentinel=False, pointer=False)
    assert version_refs.is_complete(versions, "deadbeef") is False
    assert versions.sentinel("deadbeef") == versions.dir("deadbeef") / ".complete"
    versions.sentinel("deadbeef").write_text("", encoding="utf-8")
    assert version_refs.is_complete(versions, "deadbeef") is True


@pytest.mark.parametrize("corrupt", ["", "  ", "..", "a/b", "../escape", "x\ny"])
def test_read_current_sha_corrupt(tmp_path, corrupt):
    """Empty / dotdot / path-separator pointer content → None (never escapes)."""
    versions = ProjectLayout.at(tmp_path).versions
    versions.root.mkdir(parents=True, exist_ok=True)
    versions.current_pointer.write_text(corrupt, encoding="utf-8")
    assert version_refs.read_current_sha(versions) is None
