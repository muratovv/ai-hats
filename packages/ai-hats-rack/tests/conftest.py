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
    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    monkeypatch.delenv("AI_HATS_PROJECT_DIR", raising=False)
    monkeypatch.setenv("AI_HATS_PLAN_ACK", "1")
    yield


@pytest.fixture(autouse=True)
def _isolate_project_root(tmp_path, monkeypatch):
    """Run every rack test from its own ``tmp_path`` sandbox (HATS-1545).

    These tests hand the CLI a throwaway backlog (``--tasks-dir <tmp_path>/tasks``),
    but the *project* root is resolved by walking up from the caller's cwd — and
    ``--tasks-dir`` only overrides the backlog, never the anchor. Left at the
    developer's cwd, the anchor was whatever checkout the runner happened to sit
    in, and from a LINKED worktree ``find_project_root`` deliberately hops to the
    MAIN one (HATS-1038 C2). Every wired-kernel dependency read off that anchor —
    the composed role behind the ``checks`` channel above all — was therefore
    sourced from a *sibling working tree*, so an unrelated checkout's library
    could abort a transition here.

    Chdir'ing into the sandbox makes the anchor the same throwaway directory the
    backlog already lives in: no markers above it, so resolution stops there and
    the test reads only what it created itself.
    """  # comment-length: allow — why --tasks-dir alone is not isolation is the point
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def tasks_dir(tmp_path) -> Path:
    return tmp_path / "tasks"


@pytest.fixture
def cwd(tmp_path) -> Path:
    return tmp_path
