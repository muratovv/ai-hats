"""HATS-518: ``assert_head_is_canonical_base`` guard for ``wt create``.

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

from ai_hats.paths import worktrees_dir
from ai_hats_wt import WorktreeBaseBranchError, assert_head_is_canonical_base
from ai_hats_wt.manager import CANONICAL_BASE_BRANCHES


pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
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
        assert_head_is_canonical_base(master_project)  # no raise

    def test_passes_on_main_when_no_master(self, main_project: Path) -> None:
        head = _git(main_project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert head == "main"
        # No `master` branch exists — `main` is the sole canonical.
        assert_head_is_canonical_base(main_project)  # no raise

    def test_passes_on_master_when_both_exist(self, master_project: Path) -> None:
        # Create `main` alongside `master`. HEAD still on master → pass.
        _git(master_project, "branch", "main")
        assert_head_is_canonical_base(master_project)  # no raise

    def test_passes_on_main_when_both_exist(self, master_project: Path) -> None:
        # Both branches exist, HEAD on `main` → pass.
        _git(master_project, "branch", "main")
        _git(master_project, "checkout", "main")
        assert_head_is_canonical_base(master_project)  # no raise


# ---------------------------------------------------------------------------
# Refuses on feature branches
# ---------------------------------------------------------------------------


class TestRefuses:
    def test_raises_on_feature_branch(self, master_project: Path) -> None:
        _git(master_project, "checkout", "-b", "feat/foo")
        with pytest.raises(WorktreeBaseBranchError) as excinfo:
            assert_head_is_canonical_base(master_project)
        exc = excinfo.value
        assert exc.current == "feat/foo"
        assert exc.canonical == ["master"]
        # Error message surfaces both the current branch and the canon.
        msg = str(exc)
        assert "feat/foo" in msg
        assert "master" in msg

    def test_raises_on_task_branch_with_both_canonicals(
        self,
        master_project: Path,
    ) -> None:
        _git(master_project, "branch", "main")
        _git(master_project, "checkout", "-b", "task/hats-007")
        with pytest.raises(WorktreeBaseBranchError) as excinfo:
            assert_head_is_canonical_base(master_project)
        exc = excinfo.value
        assert exc.current == "task/hats-007"
        # Canonical list reflects what actually exists, in priority order.
        # Order mirrors `CANONICAL_BASE_BRANCHES` — if you reorder the
        # tuple (e.g. project-wide switch to main-first), update here too.
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
        assert_head_is_canonical_base(master_project)  # no raise

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
        assert_head_is_canonical_base(project)  # no raise

    def test_noop_on_non_git_dir(self, tmp_path: Path) -> None:
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert_head_is_canonical_base(plain)  # no raise


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

    def test_refuses_on_feature_branch(self, master_project: Path, monkeypatch) -> None:
        from click.testing import CliRunner

        from ai_hats.cli import main as cli_main
        from ai_hats_wt import WorktreeManager

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
        from ai_hats_wt import WorktreeManager

        monkeypatch.chdir(master_project)
        result = CliRunner().invoke(cli_main, ["wt", "create", "task/probe"])
        try:
            assert result.exit_code == 0, result.output
            wt = WorktreeManager.load_for_branch(
                master_project,
                "task/probe",
                state_dir=worktrees_dir(master_project),
            )
            assert wt is not None
        finally:
            wt = WorktreeManager.load_for_branch(
                master_project,
                "task/probe",
                state_dir=worktrees_dir(master_project),
            )
            if wt is not None:
                wt.cleanup()


# ---------------------------------------------------------------------------
# Integration: TaskManager transition execute
# ---------------------------------------------------------------------------


class TestTransitionExecute:
    """`task transition <ID> execute` must refuse and keep the card unchanged."""

    @pytest.fixture
    def task_mgr(self, master_project: Path):
        """Project with `.agent/` layout + a task seeded directly in PLAN.

        Seeds via ``create_task`` + direct file write rather than walking
        ``transition(BRAINSTORM → PLAN)`` so the test isn't coupled to the
        scaffold-creation side effect of that transition (which could
        gain refusal semantics later for unrelated reasons).
        """
        from ai_hats.models import TaskState
        from ai_hats_tracker.state import TaskManager
        from ai_hats.tracker_wiring import tracker_paths
        from ai_hats.wt_effects import WtWorktreeEffects

        (master_project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
        (master_project / ".agent" / "STATE.md").write_text("")

        mgr = TaskManager(
            master_project,
            prefix="T",
            strict_plan_check=False,
            layout=tracker_paths(master_project),
            worktree_effects=WtWorktreeEffects(master_project),
        )
        mgr.create_task("T-1", "HATS-518 probe")
        # Promote the seeded card to PLAN by direct file mutation — avoids
        # the BRAINSTORM→PLAN transition path entirely.
        card = mgr.get_task("T-1")
        card.state = TaskState.PLAN
        mgr._save_task(card)
        return master_project, mgr

    def test_refuses_and_leaves_card_in_plan(self, task_mgr) -> None:
        from ai_hats.models import TaskState

        master_project, mgr = task_mgr
        # Park HEAD on a feature branch.
        _git(master_project, "checkout", "-b", "feat/parking")

        with pytest.raises(WorktreeBaseBranchError):
            mgr.transition("T-1", TaskState.EXECUTE)

        # Card stays in PLAN — _save_task was never reached.
        assert mgr.get_task("T-1").state == TaskState.PLAN

    def test_refuses_even_with_force(self, task_mgr) -> None:
        """`--force` overrides the FSM, NOT the safety contract (HATS-518).

        Same precedent as merge / discard refusals (HATS-481): destructive
        overrides bypass the state-machine arrow, not the underlying
        invariant. If the operator genuinely wants a non-canonical merge
        target, they must checkout that branch in the main repo first.
        """
        from ai_hats.models import TaskState

        master_project, mgr = task_mgr
        _git(master_project, "checkout", "-b", "feat/parking")

        with pytest.raises(WorktreeBaseBranchError):
            mgr.transition(
                "T-1",
                TaskState.EXECUTE,
                force=True,
                reason="trying to bypass HATS-518 (must fail)",
            )

        assert mgr.get_task("T-1").state == TaskState.PLAN

    def test_succeeds_when_head_is_master(self, task_mgr) -> None:
        from ai_hats.models import TaskState
        from ai_hats.paths import worktrees_dir
        from ai_hats_wt import WorktreeManager

        master_project, mgr = task_mgr
        state_dir = worktrees_dir(master_project)  # D4: where the seam persists state
        try:
            t, _ = mgr.transition("T-1", TaskState.EXECUTE)
            assert t.state == TaskState.EXECUTE
            # The wired seam really created the worktree — a silently degraded
            # pure-FSM pass must fail here (HATS-866 review).
            assert WorktreeManager.load_for_task(master_project, "T-1", state_dir=state_dir) is not None
        finally:
            wt = WorktreeManager.load_for_task(master_project, "T-1", state_dir=state_dir)
            if wt is not None:
                wt.cleanup()
