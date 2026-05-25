"""HATS-518: ``_assert_head_is_canonical_base`` guard for ``wt create``.

Covers the guard's pure semantics:

* Passes on canonical base branches (master, main).
* Passes when both canonical names exist and HEAD is one of them.
* Passes with a degenerate base: detached HEAD, missing ``.git``, repo
  without any canonical name.
* Raises :class:`WorktreeBaseBranchError` with both ``current`` and
  ``canonical`` fields populated when HEAD is on a feature branch.

Integration with ``WorktreeManager.create()`` / CLI commands lives in
``tests/test_worktree_cli.py`` and ``tests/e2e/test_wt_create_base_guard_e2e.py``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.worktree import (
    CANONICAL_BASE_BRANCHES,
    WorktreeBaseBranchError,
    _assert_head_is_canonical_base,
)


pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True,
    )


@pytest.fixture
def master_project(tmp_path: Path) -> Path:
    """Repo on a fresh ``master`` branch with one commit."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


@pytest.fixture
def main_project(tmp_path: Path) -> Path:
    """Repo on a fresh ``main`` branch (no ``master`` present)."""
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


# ---------------------------------------------------------------------------
# Happy paths — no raise
# ---------------------------------------------------------------------------


class TestPasses:
    def test_passes_on_master(self, master_project: Path) -> None:
        # Sanity: HEAD is exactly "master".
        head = _git(master_project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert head == "master"
        _assert_head_is_canonical_base(master_project)  # no raise

    def test_passes_on_main_when_no_master(self, main_project: Path) -> None:
        head = _git(main_project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert head == "main"
        # No `master` branch exists — `main` is the sole canonical.
        _assert_head_is_canonical_base(main_project)  # no raise

    def test_passes_on_master_when_both_exist(self, master_project: Path) -> None:
        # Create `main` alongside `master`. HEAD still on master → pass.
        _git(master_project, "branch", "main")
        _assert_head_is_canonical_base(master_project)  # no raise

    def test_passes_on_main_when_both_exist(self, master_project: Path) -> None:
        # Both branches exist, HEAD on `main` → pass.
        _git(master_project, "branch", "main")
        _git(master_project, "checkout", "main")
        _assert_head_is_canonical_base(master_project)  # no raise


# ---------------------------------------------------------------------------
# Refuses on feature branches
# ---------------------------------------------------------------------------


class TestRefuses:
    def test_raises_on_feature_branch(self, master_project: Path) -> None:
        _git(master_project, "checkout", "-b", "feat/foo")
        with pytest.raises(WorktreeBaseBranchError) as excinfo:
            _assert_head_is_canonical_base(master_project)
        exc = excinfo.value
        assert exc.current == "feat/foo"
        assert exc.canonical == ["master"]
        # Error message surfaces both the current branch and the canon.
        msg = str(exc)
        assert "feat/foo" in msg
        assert "master" in msg

    def test_raises_on_task_branch_with_both_canonicals(
        self, master_project: Path,
    ) -> None:
        _git(master_project, "branch", "main")
        _git(master_project, "checkout", "-b", "task/hats-007")
        with pytest.raises(WorktreeBaseBranchError) as excinfo:
            _assert_head_is_canonical_base(master_project)
        exc = excinfo.value
        assert exc.current == "task/hats-007"
        # Canonical list reflects what actually exists, in priority order.
        assert exc.canonical == ["master", "main"]


# ---------------------------------------------------------------------------
# No-op edge cases — pass through without raising
# ---------------------------------------------------------------------------


class TestNoopEdgeCases:
    def test_noop_on_detached_head(self, master_project: Path) -> None:
        # Detach HEAD onto the current commit. `rev-parse --abbrev-ref HEAD`
        # returns the literal "HEAD" — guard should pass through, not raise.
        sha = _git(master_project, "rev-parse", "HEAD").stdout.strip()
        _git(master_project, "checkout", sha)
        _assert_head_is_canonical_base(master_project)  # no raise

    def test_noop_when_no_canonical_exists(self, tmp_path: Path) -> None:
        # Repo with only a `develop` branch — no `master`, no `main`.
        # Guard has no canon to compare against → pass through.
        project = tmp_path / "exotic"
        project.mkdir()
        _git(project, "init", "-b", "develop")
        _git(project, "config", "user.email", "test@test.com")
        _git(project, "config", "user.name", "Test")
        (project / "README.md").write_text("# Test\n")
        _git(project, "add", ".")
        _git(project, "commit", "-m", "init")
        _assert_head_is_canonical_base(project)  # no raise

    def test_noop_on_non_git_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        _assert_head_is_canonical_base(plain)  # no raise


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_canonical_base_branches_constant() -> None:
    # Priority order matters: `master` first, `main` second. The current
    # contract is intentional (KISS, HATS-518) — if you swap or extend
    # this list, update the guard's tests too.
    assert CANONICAL_BASE_BRANCHES == ("master", "main")


# ---------------------------------------------------------------------------
# Integration: CLI surface
# ---------------------------------------------------------------------------


class TestCliWtCreate:
    """`ai-hats wt create` must surface the guard with red text + exit 1."""

    def test_refuses_on_feature_branch(
        self, master_project: Path, monkeypatch
    ) -> None:
        from click.testing import CliRunner

        from ai_hats.cli import main as cli_main
        from ai_hats.worktree import WorktreeManager

        _git(master_project, "checkout", "-b", "feat/foo")
        # `_project_dir()` walks up from cwd looking for `.git` — chdir works.
        monkeypatch.chdir(master_project)

        result = CliRunner().invoke(cli_main, ["wt", "create", "task/probe"])

        assert result.exit_code == 1, result.output
        assert "Refused" in result.output
        assert "feat/foo" in result.output
        assert "master" in result.output
        # No worktree should have been created — no leak.
        assert WorktreeManager.load_for_branch(master_project, "task/probe") is None

    def test_succeeds_on_master(self, master_project: Path, monkeypatch) -> None:
        from click.testing import CliRunner

        from ai_hats.cli import main as cli_main
        from ai_hats.worktree import WorktreeManager

        monkeypatch.chdir(master_project)
        result = CliRunner().invoke(cli_main, ["wt", "create", "task/probe"])
        try:
            assert result.exit_code == 0, result.output
            wt = WorktreeManager.load_for_branch(master_project, "task/probe")
            assert wt is not None
        finally:
            wt = WorktreeManager.load_for_branch(master_project, "task/probe")
            if wt is not None:
                wt.cleanup()


# ---------------------------------------------------------------------------
# Integration: TaskManager transition execute
# ---------------------------------------------------------------------------


class TestTransitionExecute:
    """`task transition <ID> execute` must refuse and keep the card unchanged."""

    def _seed_task_in_plan(self, project: Path, task_id: str = "T-1") -> None:
        from ai_hats.state import TaskManager

        mgr = TaskManager(project, prefix="T", strict_plan_check=False)
        mgr.create_task(task_id, "HATS-518 probe")
        mgr.transition(task_id, __import__("ai_hats.models", fromlist=["TaskState"]).TaskState.PLAN)
        # Fill scaffold so EXECUTE doesn't trip the empty-plan guard if
        # strict mode ever re-enables — content arbitrary.
        plan_path = mgr.tasks_dir / task_id / "plan.md"
        if plan_path.exists():
            plan_path.write_text("# Plan\n\nNon-empty plan body for tests.\n")

    def test_refuses_and_leaves_card_in_plan(
        self, master_project: Path
    ) -> None:
        from ai_hats.models import TaskState
        from ai_hats.state import TaskManager

        # Project needs the .agent layout for TaskManager.
        (master_project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
        (master_project / ".agent" / "STATE.md").write_text("")

        self._seed_task_in_plan(master_project, "T-1")
        # Park HEAD on a feature branch.
        _git(master_project, "checkout", "-b", "feat/parking")

        mgr = TaskManager(master_project, prefix="T", strict_plan_check=False)
        with pytest.raises(WorktreeBaseBranchError):
            mgr.transition("T-1", TaskState.EXECUTE)

        # Card stays in PLAN — _save_task was never reached.
        t = mgr.get_task("T-1")
        assert t.state == TaskState.PLAN

    def test_succeeds_when_head_is_master(
        self, master_project: Path
    ) -> None:
        from ai_hats.models import TaskState
        from ai_hats.state import TaskManager
        from ai_hats.worktree import WorktreeManager

        (master_project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
        (master_project / ".agent" / "STATE.md").write_text("")

        self._seed_task_in_plan(master_project, "T-1")
        mgr = TaskManager(master_project, prefix="T", strict_plan_check=False)
        try:
            t = mgr.transition("T-1", TaskState.EXECUTE)
            assert t.state == TaskState.EXECUTE
        finally:
            wt = WorktreeManager.load_for_task(master_project, "T-1")
            if wt is not None:
                wt.cleanup()
