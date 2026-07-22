"""Unit tests for universal SurfaceGuard across all provider surfaces (HATS-1105)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_hats.harness.surface_guard import SurfaceGuard, SurfaceGuardError
from ai_hats_wt import IsolationMode


def test_pre_flight_check_passes_in_isolated_worktree(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    work_dir = tmp_path / "worktree"
    project_dir.mkdir()
    work_dir.mkdir()

    # Should not raise for isolated work_dir
    SurfaceGuard.pre_flight_check(
        project_dir=project_dir,
        work_dir=work_dir,
        isolation_mode=IsolationMode.DISCARD.value,
        provider_name="agy",
    )
    SurfaceGuard.pre_flight_check(
        project_dir=project_dir,
        work_dir=work_dir,
        isolation_mode=IsolationMode.SQUASH.value,
        provider_name="claude",
    )


def test_pre_flight_check_refuses_main_project_dir_when_isolated(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    with pytest.raises(SurfaceGuardError, match="equals main project_dir"):
        SurfaceGuard.pre_flight_check(
            project_dir=project_dir,
            work_dir=project_dir,
            isolation_mode=IsolationMode.DISCARD.value,
            provider_name="agy",
        )

    with pytest.raises(SurfaceGuardError, match="equals main project_dir"):
        SurfaceGuard.pre_flight_check(
            project_dir=project_dir,
            work_dir=project_dir,
            isolation_mode=IsolationMode.BRANCH.value,
            provider_name="cline",
        )



def test_post_flight_guard_runs_without_error(tmp_path: Path) -> None:
    work_dir = tmp_path / "worktree"
    work_dir.mkdir()
    session = MagicMock()
    session.session_id = "sid-test-guard"

    # Should run smoothly without raising
    SurfaceGuard.post_flight_guard(
        session=session,
        work_dir=work_dir,
        provider_name="agy",
    )
