"""Tests for state management — task lifecycle, work logs, indexes."""

import subprocess

import pytest
from pathlib import Path

from ai_hats.models import TaskState
from ai_hats.state import TaskManager
from ai_hats.worktree import WorktreeManager


@pytest.fixture
def mgr(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    return TaskManager(project)


def test_create_task_with_priority(mgr):
    t = mgr.create_task("T-1", "High priority task", priority="high", reviewer="agent")
    assert t.priority == "high"
    assert t.reviewer == "agent"
    assert t.state == TaskState.BRAINSTORM


def test_auto_id(mgr):
    mgr.create_task("HATS-001", "First")
    mgr.create_task("HATS-002", "Second")
    next_id = mgr.next_id()
    assert next_id == "HATS-003"


def test_auto_id_empty(mgr):
    assert mgr.next_id() == "HATS-001"


def test_work_log(mgr):
    mgr.create_task("T-1", "Task with logs")
    t = mgr.log_work("T-1", "Started implementation", session_id="sess-001")
    assert len(t.work_log) == 1
    assert "sess-001" in t.work_log[0].message
    assert "Started implementation" in t.work_log[0].message

    t = mgr.log_work("T-1", "Fixed bug")
    assert len(t.work_log) == 2


def test_work_log_persists(mgr):
    mgr.create_task("T-1", "Task")
    mgr.log_work("T-1", "Entry 1")
    mgr.log_work("T-1", "Entry 2")

    # Reload from disk
    t = mgr.get_task("T-1")
    assert len(t.work_log) == 2
    assert "Entry 1" in t.work_log[0].message


def test_plan_scaffold_created_on_transition(mgr):
    mgr.create_task("T-1", "Plan me")
    mgr.transition("T-1", TaskState.PLAN)

    plan_path = mgr.tasks_dir / "T-1" / "plan.md"
    assert plan_path.exists()
    content = plan_path.read_text()
    assert "T-1" in content
    assert "Plan me" in content
    assert "## Steps" in content


def test_plan_scaffold_not_overwritten(mgr):
    mgr.create_task("T-1", "Plan me")

    # Create custom plan before transition
    plan_path = mgr.tasks_dir / "T-1" / "plan.md"
    plan_path.write_text("# My custom plan")

    mgr.transition("T-1", TaskState.PLAN)

    assert plan_path.read_text() == "# My custom plan"


def test_completed_at_set_on_done(mgr):
    mgr.create_task("T-1", "Complete me")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)
    mgr.transition("T-1", TaskState.DOCUMENT)
    mgr.transition("T-1", TaskState.REVIEW)
    mgr.transition("T-1", TaskState.DONE)

    t = mgr.get_task("T-1")
    assert t.completed_at != ""
    assert t.state == TaskState.DONE


def test_final_state(mgr):
    mgr.create_task("T-1", "Review me")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)
    mgr.set_final_state("T-1", "Implemented feature X with full test coverage")
    mgr.transition("T-1", TaskState.DOCUMENT)
    mgr.transition("T-1", TaskState.REVIEW)

    t = mgr.get_task("T-1")
    assert t.final_state == "Implemented feature X with full test coverage"


def test_backlog_md_generated(mgr):
    mgr.create_task("T-1", "First task", priority="high")
    mgr.create_task("T-2", "Second task")

    assert mgr.backlog_md_path.exists()
    content = mgr.backlog_md_path.read_text()
    assert "T-1" in content
    assert "T-2" in content
    assert "high" in content
    assert "| ID |" in content  # Table header


def test_state_md_shows_priority(mgr):
    mgr.create_task("T-1", "Important", priority="high")

    content = mgr.state_md_path.read_text()
    assert "[high]" in content


def test_sync(mgr):
    mgr.create_task("T-1", "Task 1")
    mgr.create_task("T-2", "Task 2")

    # Manually delete backlog.md to test sync
    mgr.backlog_md_path.unlink()
    assert not mgr.backlog_md_path.exists()

    count = mgr.sync()
    assert count == 2
    assert mgr.backlog_md_path.exists()


def test_tags_on_task(mgr):
    t = mgr.create_task("T-1", "Tagged task", tags=["p0", "mvp"])
    assert t.tags == ["p0", "mvp"]

    t = mgr.get_task("T-1")
    assert t.tags == ["p0", "mvp"]


def test_full_lifecycle_with_logs(mgr):
    """Full state machine walkthrough with work logging."""
    mgr.create_task("T-1", "End-to-end", priority="high", reviewer="user")

    mgr.log_work("T-1", "Brainstorming approaches")
    mgr.transition("T-1", TaskState.PLAN)

    mgr.log_work("T-1", "Plan written")
    mgr.transition("T-1", TaskState.EXECUTE)

    mgr.log_work("T-1", "Implementation complete")
    mgr.set_final_state("T-1", "Feature implemented and tested")
    mgr.transition("T-1", TaskState.DOCUMENT)

    mgr.log_work("T-1", "Docs updated")
    mgr.transition("T-1", TaskState.REVIEW)

    mgr.log_work("T-1", "Review passed")
    mgr.transition("T-1", TaskState.DONE)

    t = mgr.get_task("T-1")
    assert t.state == TaskState.DONE
    assert t.completed_at != ""
    assert t.final_state == "Feature implemented and tested"
    assert len(t.work_log) == 5


# -- Worktree integration tests --


def _init_git(path: Path) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True)
    (path / "README.md").write_text("# test")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True, check=True)


@pytest.fixture
def git_mgr(tmp_path):
    """TaskManager backed by a real git repo."""
    project = tmp_path / "project"
    project.mkdir()
    _init_git(project)
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    return TaskManager(project)


def test_execute_creates_worktree(git_mgr):
    git_mgr.create_task("T-1", "Work in worktree")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_active(git_mgr.project_dir)
    assert active is not None
    assert active.branch_name == "task/t-1"
    assert active.worktree_path.exists()


def test_done_merges_worktree(git_mgr):
    git_mgr.create_task("T-1", "Merge on done")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    # Make a change in the worktree
    active = WorktreeManager.load_active(git_mgr.project_dir)
    (active.worktree_path / "new_file.txt").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=str(active.worktree_path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "add file"], cwd=str(active.worktree_path), capture_output=True, check=True)

    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)
    git_mgr.transition("T-1", TaskState.DONE)

    # Worktree cleaned up
    assert WorktreeManager.load_active(git_mgr.project_dir) is None
    # Change merged into main
    assert (git_mgr.project_dir / "new_file.txt").exists()


def test_failed_discards_worktree(git_mgr):
    git_mgr.create_task("T-1", "Fail and discard")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_active(git_mgr.project_dir)
    wt_path = active.worktree_path
    assert wt_path.exists()

    git_mgr.transition("T-1", TaskState.FAILED)

    # Worktree removed
    assert WorktreeManager.load_active(git_mgr.project_dir) is None
    assert not wt_path.exists()


def test_execute_blocks_if_worktree_active(git_mgr):
    git_mgr.create_task("T-1", "First task")
    git_mgr.create_task("T-2", "Second task")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    git_mgr.transition("T-2", TaskState.PLAN)
    with pytest.raises(ValueError, match="Active worktree exists"):
        git_mgr.transition("T-2", TaskState.EXECUTE)


def test_execute_reuses_worktree_after_blocked(git_mgr):
    git_mgr.create_task("T-1", "Block and resume")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_active(git_mgr.project_dir)
    wt_path = active.worktree_path

    git_mgr.transition("T-1", TaskState.BLOCKED)
    # Worktree still active after blocked
    assert WorktreeManager.load_active(git_mgr.project_dir) is not None

    git_mgr.transition("T-1", TaskState.EXECUTE)
    # Same worktree reused
    active2 = WorktreeManager.load_active(git_mgr.project_dir)
    assert active2.worktree_path == wt_path


def test_no_worktree_in_non_git_project(mgr):
    """Non-git projects skip worktree creation silently."""
    mgr.create_task("T-1", "No git here")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_active(mgr.project_dir)
    assert active is None
