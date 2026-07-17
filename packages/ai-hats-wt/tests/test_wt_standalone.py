"""ADR-0013 D9 — standalone consumability smoke test for the ``wt`` core.

Proves the extracted worktree engine is a *self-contained, consumable* surface,
not merely an isolated one: a third party can ``from ai_hats_wt import
WorktreeManager`` and drive the full create → merge / create → discard lifecycle
on a bare ``git init`` with **no** ``ai-hats.yaml``, **no** composition, **no**
tracker — using the **no-op** lifecycle bundle and the **project-local**
state-dir fallback.

It deliberately imports ONLY the ``ai_hats_wt`` public surface (the
``__init__.__all__`` D9 export) — never a submodule (``ai_hats_wt.manager`` /
``.locks``) and never an ai-hats accretion (``ai_hats.paths`` / ``state`` / …).
That standalone-surface sufficiency is the property this test guards and the
unit suite (``test_worktree.py``, which reaches into internals + imports
``ai_hats.paths``) does not. Pins ADR-0013 §D9 + matrix rows S1 / S5 / S13.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import ai_hats_wt as wt
from ai_hats_wt import NOOP_LIFECYCLE, WorktreeManager


# Real ``git`` subprocesses → ``integration`` (out of the fast cross-version unit
# matrix, which configures no git identity); ``smoke`` → collected by the
# maintainer e2e+smoke gate (``scripts/run-e2e-gate.sh``) per the D9 framing.
pytestmark = [pytest.mark.integration, pytest.mark.smoke]

# The minimal public surface a standalone consumer needs to drive the lifecycle.
_STANDALONE_SURFACE = {"WorktreeManager", "IsolationMode", "NOOP_LIFECYCLE"}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # HATS-886: drop inherited GIT_* so an ambient GIT_DIR/GIT_WORK_TREE (which
    # the merge-smoke gate's enclosing `git merge` exports at the REAL repo)
    # cannot retarget these plumbing calls off `cwd` onto real master.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """A plain git repo with one commit — no ai-hats.yaml / tracker / composition."""
    project = tmp_path / "standalone"
    project.mkdir()
    _git(project, "init")
    # HATS-886 tripwire: refuse to add/commit unless the repo we just made is
    # this tmp dir's own `.git`. If an ambient GIT_DIR slipped past the env-strip
    # `git init` would have (re)targeted the real repo — fail loud, before harm.
    git_dir = Path(_git(project, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    assert git_dir == (project / ".git").resolve(), (
        f"bare_repo escaped its sandbox: git dir is {git_dir}, not {project / '.git'}"
    )
    assert tmp_path.resolve() in git_dir.parents, (
        f"bare_repo git dir {git_dir} is not under tmp_path {tmp_path.resolve()}"
    )
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
        f"ai_hats_wt.__all__ must export the standalone-consumer surface "
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


def test_merge_requires_consent_standalone(
    bare_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HATS-1019: merge is default-deny without AI_HATS_MERGE_ACK=1."""
    monkeypatch.delenv("AI_HATS_MERGE_ACK", raising=False)
    mgr = WorktreeManager(
        bare_repo, branch_name="standalone/gated", lifecycle=NOOP_LIFECYCLE
    )
    wt_path = mgr.create()
    mgr.save_state()
    _commit_in_worktree(wt_path, "gated.txt", "needs review")

    with pytest.raises(wt.WorktreeMergeConsentError):
        mgr.merge()

    assert wt_path.exists(), "consent refusal must preserve the worktree"
    assert not (bare_repo / "gated.txt").exists()
    mgr.discard()


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


def test_git_env_isolation_regression(tmp_path: Path, monkeypatch) -> None:
    """HATS-886 gate: this module's git plumbing must ignore an ambient GIT_DIR.

    Points GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE at a throwaway "victim" repo (as
    the merge-smoke gate's enclosing ``git merge`` exports the real repo), then
    runs the same init/config/commit plumbing ``bare_repo`` uses, in an unrelated
    dir. The env-strip in :func:`_git` must confine it; reverting Fix #1 lets the
    commit land in the victim, which the unchanged-HEAD assertion catches (RED).
    """
    victim = tmp_path / "victim"
    victim.mkdir()
    _git(victim, "init")
    _git(victim, "config", "user.email", "victim@example.com")  # ai-hats: allow-secret
    _git(victim, "config", "user.name", "Victim Repo")
    (victim / "keep.txt").write_text("keep\n")
    _git(victim, "add", ".")
    _git(victim, "commit", "-m", "victim baseline")
    before_head = _git(victim, "rev-parse", "HEAD").stdout.strip()
    before_count = _git(victim, "rev-list", "--count", "HEAD").stdout.strip()

    # Simulate the git-merge context that exports the victim as the active repo.
    monkeypatch.setenv("GIT_DIR", str((victim / ".git").resolve()))
    monkeypatch.setenv("GIT_WORK_TREE", str(victim.resolve()))
    monkeypatch.setenv("GIT_INDEX_FILE", str((victim / ".git" / "index").resolve()))

    project = tmp_path / "standalone"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "consumer@example.com")  # ai-hats: allow-secret
    _git(project, "config", "user.name", "Standalone Consumer")
    _git(project, "commit", "--allow-empty", "-m", "standalone probe")

    after_head = _git(victim, "rev-parse", "HEAD").stdout.strip()
    after_count = _git(victim, "rev-list", "--count", "HEAD").stdout.strip()
    assert after_head == before_head and after_count == before_count, (
        "subprocess-git honoured an ambient GIT_DIR and mutated the env-pointed "
        f"repo (HATS-886): count {before_count} -> {after_count}, "
        f"HEAD {before_head[:12]} -> {after_head[:12]}"
    )


def test_reclaim_if_clean_reclaims_empty_branch(bare_repo: Path) -> None:
    """HATS-979: an epicified task's worktree with no own commits (branch tip is
    the base) is reclaimed — dir + branch removed, nothing merged."""
    mgr = WorktreeManager(bare_repo, branch_name="task/epic-empty", lifecycle=NOOP_LIFECYCLE)
    wt_path = mgr.create()
    mgr.save_state()
    assert wt_path.is_dir()

    assert mgr.reclaim_if_clean() is True
    assert not wt_path.exists()
    assert _git(bare_repo, "branch", "--list", "task/epic-empty").stdout.strip() == ""


def test_reclaim_if_clean_keeps_unmerged_commits(bare_repo: Path) -> None:
    """Work-preservation: a branch carrying own commits not in the base is KEPT."""
    mgr = WorktreeManager(bare_repo, branch_name="task/epic-work", lifecycle=NOOP_LIFECYCLE)
    wt_path = mgr.create()
    mgr.save_state()
    _commit_in_worktree(wt_path, "wip.txt", "unmerged work")

    assert mgr.reclaim_if_clean() is False
    assert wt_path.exists()
    assert _git(bare_repo, "branch", "--list", "task/epic-work").stdout.strip() != ""


def test_reclaim_if_clean_keeps_dirty_tree(bare_repo: Path) -> None:
    """Work-preservation: uncommitted changes in the worktree KEEP it."""
    mgr = WorktreeManager(bare_repo, branch_name="task/epic-dirty", lifecycle=NOOP_LIFECYCLE)
    wt_path = mgr.create()
    mgr.save_state()
    (wt_path / "scratch.txt").write_text("uncommitted")  # dirty: untracked, not committed

    assert mgr.reclaim_if_clean() is False
    assert wt_path.exists()
    assert _git(bare_repo, "branch", "--list", "task/epic-dirty").stdout.strip() != ""


def test_reclaim_if_clean_respects_injected_extra_hold(bare_repo: Path) -> None:
    """An otherwise-reclaimable (empty, git-clean) worktree is KEPT when the
    caller's ``has_extra_hold`` fires — the seam for gitignored state the git
    checks can't see (e.g. pending hunk review)."""
    mgr = WorktreeManager(bare_repo, branch_name="task/epic-held", lifecycle=NOOP_LIFECYCLE)
    wt_path = mgr.create()
    mgr.save_state()

    assert mgr.reclaim_if_clean(has_extra_hold=lambda _p: True) is False
    assert wt_path.exists()
    assert _git(bare_repo, "branch", "--list", "task/epic-held").stdout.strip() != ""
