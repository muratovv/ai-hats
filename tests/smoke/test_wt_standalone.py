"""ADR-0013 D9 — standalone consumability smoke test for the ``wt`` core.

Proves the extracted worktree engine is a *self-contained, consumable* surface,
not merely an isolated one: a third party (or the Option-A ``git filter-repo``
future) can ``from ai_hats.wt import WorktreeManager`` and drive the full
create → merge / create → discard lifecycle on a bare ``git init`` with **no**
``ai-hats.yaml``, **no** composition, **no** tracker — using the **no-op**
lifecycle bundle and the **project-local** state-dir fallback.

It deliberately imports ONLY the ``ai_hats.wt`` public surface (the
``__init__.__all__`` D9 export) — never a submodule (``ai_hats.wt.manager`` /
``.locks``) and never an ai-hats accretion (``ai_hats.paths`` / ``state`` / …).
That standalone-surface sufficiency is the property this test guards and the
unit suite (``test_worktree.py``, which reaches into internals + imports
``ai_hats.paths``) does not. Pins ADR-0013 §D9 + matrix rows S1 / S5 / S13.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ai_hats.wt as wt
from ai_hats.wt import NOOP_LIFECYCLE, WorktreeManager


# Real ``git`` subprocesses → ``integration`` (out of the fast cross-version unit
# matrix, which configures no git identity); ``smoke`` → collected by the
# maintainer e2e+smoke gate (``scripts/run-e2e-gate.sh``) per the D9 framing.
pytestmark = [pytest.mark.integration, pytest.mark.smoke]

# The minimal public surface a standalone consumer needs to drive the lifecycle.
_STANDALONE_SURFACE = {"WorktreeManager", "IsolationMode", "NOOP_LIFECYCLE"}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A plain git repo with one commit — no ai-hats.yaml / tracker / composition."""
    project = tmp_path / "standalone"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "consumer@example.com")
    _git(project, "config", "user.name", "Standalone Consumer")
    (project / "README.md").write_text("# standalone consumer\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def _commit_in_worktree(wt_path: Path, filename: str, content: str) -> None:
    _git(wt_path, "config", "user.email", "consumer@example.com")
    _git(wt_path, "config", "user.name", "Standalone Consumer")
    (wt_path / filename).write_text(content)
    _git(wt_path, "add", filename)
    _git(wt_path, "commit", "-m", f"wt: add {filename}")


def test_public_surface_is_sufficient() -> None:
    """D9: the standalone-consumer surface is exported from ``__all__``.

    RED-under-revert: narrowing ``wt/__init__.__all__`` (dropping any of the
    standalone-surface names) fails this assertion.
    """
    assert _STANDALONE_SURFACE <= set(wt.__all__), (
        f"ai_hats.wt.__all__ must export the standalone-consumer surface "
        f"{sorted(_STANDALONE_SURFACE)}; missing "
        f"{sorted(_STANDALONE_SURFACE - set(wt.__all__))}"
    )


def test_construct_with_no_ai_hats_config(bare_repo: Path) -> None:
    """The engine constructs + persists state with zero ai-hats project config.

    Asserts the project-local state-dir fallback (no ``ai_hats.paths`` import):
    ``save_state()`` lands under ``<project>/.wt`` rather than an ai-hats
    ``sessions/worktrees/`` convention dir.
    """
    assert not (bare_repo / "ai-hats.yaml").exists()
    mgr = WorktreeManager(
        bare_repo, branch_name="standalone/probe", lifecycle=NOOP_LIFECYCLE
    )
    wt_path = mgr.create()
    assert wt_path.is_dir()
    state_path = mgr.save_state()
    assert state_path.parent == bare_repo / ".wt"
    mgr.discard()


def test_create_then_merge_standalone(bare_repo: Path) -> None:
    """S5: create → commit → merge lands the change on the base branch, no hooks."""
    mgr = WorktreeManager(
        bare_repo, branch_name="standalone/feature", lifecycle=NOOP_LIFECYCLE
    )
    wt_path = mgr.create()
    assert wt_path.is_dir() and wt_path != bare_repo
    mgr.save_state()
    _commit_in_worktree(wt_path, "feature.txt", "from standalone consumer")

    mgr.merge()

    # Change landed on the base branch; worktree dir + branch are cleaned up.
    assert (bare_repo / "feature.txt").read_text() == "from standalone consumer"
    assert not wt_path.exists()
    assert (
        _git(bare_repo, "branch", "--list", "standalone/feature").stdout.strip() == ""
    )


def test_create_then_discard_standalone(bare_repo: Path) -> None:
    """S13: create → discard removes the worktree + branch and lands nothing."""
    mgr = WorktreeManager(
        bare_repo, branch_name="standalone/throwaway", lifecycle=NOOP_LIFECYCLE
    )
    wt_path = mgr.create()
    mgr.save_state()
    _commit_in_worktree(wt_path, "scratch.txt", "discard me")

    mgr.discard()

    assert not wt_path.exists()
    assert not (bare_repo / "scratch.txt").exists()
    assert (
        _git(bare_repo, "branch", "--list", "standalone/throwaway").stdout.strip() == ""
    )
