"""Gate semantics of the unclaimed-marker sweep wiring (HATS-905).

install_time-only, deferred under version skew and hard-delete mode.
"""

from __future__ import annotations

import hashlib

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.migrations import latest_step
from ai_hats.paths import PROJECT_CONFIG


@pytest.fixture
def asm(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectConfig(migration_step=latest_step()).save(project / PROJECT_CONFIG)
    return Assembler(project)


def _seed_dead_marker(project):
    payload = b"#!/bin/sh\nexit 0\n"
    digest = hashlib.sha256(payload).hexdigest()[:12]
    base = project / ".githooks"
    base.mkdir()
    (base / "stale-hook.sh").write_bytes(payload)
    marker = base / ".ai-hats-manifest"
    marker.write_text(f"# ai-hats-owner: retired-mech\n{digest}  stale-hook.sh\n")
    return marker


def test_refresh_sweeps_only_at_install_time(asm, monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(asm, "_sweep_unclaimed_markers", lambda: calls.append(True))

    asm._refresh(install_time=False, result=None)
    assert calls == []

    asm._refresh(install_time=True, result=None)
    assert calls == [True]


def test_sweep_deferred_on_version_skew(asm, monkeypatch, capsys):
    marker = _seed_dead_marker(asm.project_dir)
    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: True)

    asm._sweep_unclaimed_markers()

    assert marker.is_file()
    assert (asm.project_dir / ".githooks" / "stale-hook.sh").exists()
    err = capsys.readouterr().err
    assert "sweep deferred" in err
    assert "retired-mech" in err
    assert "self update" in err


def test_sweep_deferred_on_hard_delete_mode(asm, monkeypatch, capsys):
    marker = _seed_dead_marker(asm.project_dir)
    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: False)
    monkeypatch.setattr("ai_hats_core.safe_delete.hard_delete_mode", lambda: True)

    asm._sweep_unclaimed_markers()

    assert marker.is_file()
    err = capsys.readouterr().err
    assert "sweep deferred" in err
    assert "no undo path" in err


def test_sweep_executes_reports_and_removes_marker(asm, monkeypatch, capsys):
    marker = _seed_dead_marker(asm.project_dir)
    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: False)

    asm._sweep_unclaimed_markers()

    assert not marker.exists()
    assert not (asm.project_dir / ".githooks" / "stale-hook.sh").exists()
    out = capsys.readouterr().out
    assert "retired-mech" in out
    assert "stale-hook.sh" in out
    assert "recoverable" in out


def test_sweep_warns_on_kept_user_edited_entry(asm, monkeypatch, capsys):
    marker = _seed_dead_marker(asm.project_dir)
    (asm.project_dir / ".githooks" / "stale-hook.sh").write_bytes(b"user edit")
    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: False)

    asm._sweep_unclaimed_markers()

    assert (asm.project_dir / ".githooks" / "stale-hook.sh").read_bytes() == b"user edit"
    assert marker.is_file()
    err = capsys.readouterr().err
    assert "modified since" in err
    assert "left in place" in err


def test_living_owner_surfaces_untouched_no_output(asm, monkeypatch, capsys):
    # real registrations: git-hooks / runtime-hooks are living
    payload = b"#!/bin/sh\n"
    base = asm.project_dir / ".githooks"
    base.mkdir()
    (base / "live.sh").write_bytes(payload)
    (base / ".ai-hats-manifest").write_text("live.sh\n")
    monkeypatch.setattr(asm.hooks, "binary_behind_source", lambda: False)

    asm._sweep_unclaimed_markers()

    assert (base / "live.sh").exists()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
