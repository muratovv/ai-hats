"""E2E: a real session writes no cache into the workspace (HATS-1398, epic R16).

This is the acceptance criterion for the move, and it replaces the one the card
was filed with. The original read "an idle agy session no longer feeds
fseventsd" — but a measurement on 2026-07-31 refuted the causal chain behind it
(5 idle agy processes burned 5.4-7.1% CPU each on a workspace with zero writes
for 34 minutes, while fseventsd averaged 2.2%), so that criterion could not fail
for the right reason. What survives is the invariant: machine-only, regenerable
state does not live in the tree that watchers, ``git status``, greps and
indexers all pay for. That is falsifiable, and this test is where it is falsified.

RED under revert: before the move the session materializes into
``<project>/.agent/ai-hats/.cache/sessions/<sid>/``, and the per-sid cleanup at
session end leaves the ``.cache/sessions/`` skeleton behind — so both the
"nothing appeared" and the "workspace ends clean" assertions fail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from _helpers.hitl import drive_bare_hitl

pytestmark = pytest.mark.integration


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()} if root.is_dir() else set()


def _git_status(project: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(project),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_a_real_session_leaves_no_cache_inside_the_workspace(
    tmp_venv_project,
    requires_claude_auth,
) -> None:
    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    project = tmp_venv_project.path
    before = _files_under(project)
    # Onboarding legitimately leaves .gitignore / ai-hats.yaml untracked, so the
    # invariant is that the SESSION changes nothing — not that the tree is empty.
    git_before = _git_status(project)

    (
        drive_bare_hitl(tmp_venv_project, role="assistant")
        .expect_no_hang()
        .expect_exit_in({0, 130})
        .expect_start_banner(role="assistant", provider="claude")
    )

    appeared = _files_under(project) - before
    cache_writes = [p for p in appeared if ".cache" in p.relative_to(project).parts]
    assert not cache_writes, f"session wrote cache inside the workspace: {sorted(cache_writes)}"
    assert not (project / ".agent" / "ai-hats" / ".cache").exists()
    assert _git_status(project) == git_before


def test_the_session_materialized_into_the_out_of_tree_root(
    tmp_venv_project,
    monkeypatch,
    requires_claude_auth,
) -> None:
    """The mirror assertion: absence in the workspace must mean presence elsewhere.

    Without this half, a session that silently materialized nothing at all would
    pass the test above.
    """
    from ai_hats.paths import session_cache_root

    # Resolve against the SAME root the child was pinned to, or this asserts
    # about a directory no subprocess ever touched.
    monkeypatch.setenv("AI_HATS_CACHE_HOME", tmp_venv_project.env["AI_HATS_CACHE_HOME"])

    tmp_venv_project.run(
        "self", "init", "-r", "assistant", "-p", "claude", "--no-update", timeout=120
    ).expect_ok()

    project = tmp_venv_project.path
    cache_sessions = session_cache_root(project)
    assert not cache_sessions.exists(), "precondition: nothing built yet"

    drive_bare_hitl(tmp_venv_project, role="assistant").expect_no_hang().expect_exit_in({0, 130})

    # The per-sid dir is removed at session end; its parent is the proof the
    # session built there rather than in the project.
    assert cache_sessions.is_dir(), f"no session cache root at {cache_sessions}"
    assert not cache_sessions.is_relative_to(project)
