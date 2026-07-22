"""Unit tests for universal SurfaceGuard across all provider surfaces (HATS-1105)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_hats.harness.surface_guard import SurfaceGuard, SurfaceGuardError, SurfaceGuardResult
from ai_hats_wt import IsolationMode


def test_pre_flight_check_returns_ok_in_isolated_worktree(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    work_dir = tmp_path / "worktree"
    project_dir.mkdir()
    work_dir.mkdir()

    res1 = SurfaceGuard.pre_flight_check(
        project_dir=project_dir,
        work_dir=work_dir,
        isolation_mode=IsolationMode.DISCARD,
        provider_name="agy",
    )
    assert res1.is_ok
    assert res1.error_reason == ""

    res2 = SurfaceGuard.pre_flight_check(
        project_dir=project_dir,
        work_dir=work_dir,
        isolation_mode=IsolationMode.SQUASH,
        provider_name="claude",
    )
    assert res2.is_ok
    assert res2.error_reason == ""


def test_pre_flight_check_returns_error_for_main_project_dir(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    res = SurfaceGuard.pre_flight_check(
        project_dir=project_dir,
        work_dir=project_dir,
        isolation_mode=IsolationMode.DISCARD,
        provider_name="agy",
    )
    assert not res.is_ok
    assert "equals main project_dir" in res.error_reason

    with pytest.raises(SurfaceGuardError, match="equals main project_dir"):
        res.unwrap()


def test_post_flight_guard_returns_ok(tmp_path: Path) -> None:
    work_dir = tmp_path / "worktree"
    work_dir.mkdir()
    session = MagicMock()
    session.session_id = "sid-test-guard"

    res = SurfaceGuard.post_flight_guard(
        session=session,
        work_dir=work_dir,
        provider_name="agy",
    )
    assert res.is_ok
    res.unwrap()
