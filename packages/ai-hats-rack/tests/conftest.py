from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make rack_testkit importable regardless of pytest's rootdir/sys.path mode.
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _isolate_session_env(monkeypatch):
    """Clear ambient ``AI_HATS_SESSION_ID`` / ``AI_HATS_ROOT_PID`` per test (HATS-1049).

    Mirrors the main suite's HATS-982 fixture: the HATS-955 single-slot ownership
    check resolves the actor from ``AI_HATS_SESSION_ID``. Run inside a live ai-hats
    session (which exports it), rack transition tests that drive cross-task edges
    hit ``ownership-single-slot`` and fail — absent in CI. Clearing it makes every
    rack test resolve with no ambient session, as CI does.
    """
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    monkeypatch.delenv("AI_HATS_ROOT_PID", raising=False)
    monkeypatch.setenv("AI_HATS_PLAN_ACK", "1")
    yield


@pytest.fixture
def tasks_dir(tmp_path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def cwd(tmp_path) -> Path:
    return tmp_path
