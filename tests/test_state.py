"""Tests for state management — task lifecycle, work logs, indexes."""

import subprocess

import pytest
from pathlib import Path

from ai_hats.models import TaskState
from ai_hats.paths import worktrees_dir
from ai_hats.state import EmptyPlanError, TaskManager
from ai_hats.wt import WorktreeManager


pytestmark = pytest.mark.integration


@pytest.fixture
def mgr(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    return TaskManager(project, strict_plan_check=False)


def test_create_task_with_priority(mgr):
    t, _ = mgr.create_task("T-1", "High priority task", priority="high", reviewer="agent")
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


def test_auto_id_respects_custom_prefix(tmp_path):
    project = tmp_path / "project"
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    mgr = TaskManager(project, prefix="ACME")
    assert mgr.next_id() == "ACME-001"
    mgr.create_task("ACME-001", "First")
    assert mgr.next_id() == "ACME-002"


def test_auto_id_ignores_foreign_prefix(tmp_path):
    """next_id for prefix X ignores tasks authored under prefix Y."""
    project = tmp_path / "project"
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    legacy = TaskManager(project, prefix="HATS")
    legacy.create_task("HATS-010", "Legacy")

    fresh = TaskManager(project, prefix="TASK")
    assert fresh.next_id() == "TASK-001"


def test_write_op_refused_at_non_project_root(tmp_path, monkeypatch):
    """HATS-839: a TaskManager write op against a non-project root raises
    NotAnAiHatsProjectError and bootstraps no phantom .agent/ tracker."""
    from ai_hats.paths import NotAnAiHatsProjectError

    monkeypatch.delenv("AI_HATS_DIR", raising=False)
    stray = tmp_path / "stray"  # no .agent/, no ai-hats.yaml
    stray.mkdir()
    mgr = TaskManager(stray, strict_plan_check=False)
    with pytest.raises(NotAnAiHatsProjectError):
        mgr.create_task("X-1", "should be refused")
    assert not (stray / ".agent").exists(), "phantom tracker bootstrapped at a stray root"


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


def _walk_to_done(mgr, task_id: str) -> None:
    mgr.transition(task_id, TaskState.PLAN)
    mgr.transition(task_id, TaskState.EXECUTE)
    mgr.transition(task_id, TaskState.DOCUMENT)
    mgr.transition(task_id, TaskState.REVIEW)
    mgr.transition(task_id, TaskState.DONE)


def test_reopen_done_to_execute(mgr):
    """HATS-328: DONE → EXECUTE is a valid reopen path."""
    mgr.create_task("T-1", "Reopen me")
    _walk_to_done(mgr, "T-1")

    t, _ = mgr.transition("T-1", TaskState.EXECUTE)
    assert t.state == TaskState.EXECUTE


def test_reopen_clears_completed_at(mgr):
    """Reopening from DONE clears the completion timestamp."""
    mgr.create_task("T-1", "Reopen me")
    _walk_to_done(mgr, "T-1")
    assert mgr.get_task("T-1").completed_at != ""

    mgr.transition("T-1", TaskState.EXECUTE)
    assert mgr.get_task("T-1").completed_at == ""


def test_reopen_logs_work_entry(mgr):
    """Reopen automatically appends a work-log entry for audit."""
    mgr.create_task("T-1", "Reopen me")
    _walk_to_done(mgr, "T-1")
    before = len(mgr.get_task("T-1").work_log)

    mgr.transition("T-1", TaskState.EXECUTE)
    entries = mgr.get_task("T-1").work_log
    assert len(entries) == before + 1
    assert "Reopened from done" in entries[-1].message


def test_final_state(mgr):
    mgr.create_task("T-1", "Review me")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)
    mgr.transition("T-1", TaskState.DOCUMENT)
    # final_state rides the review transition's lock window (HATS-723).
    mgr.transition(
        "T-1",
        TaskState.REVIEW,
        final_state="Implemented feature X with full test coverage",
    )

    t = mgr.get_task("T-1")
    assert t.final_state == "Implemented feature X with full test coverage"


def test_final_state_not_written_when_transition_fails(tmp_path):
    """HATS-723: a transition that raises must not half-apply final_state.

    The old CLI wrote final_state in its own lock BEFORE transitioning; a
    later raise left the card mutated. Now final_state rides the transition's
    single lock window, so a failed transition persists nothing.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    strict = TaskManager(project, prefix="T", strict_plan_check=True)

    strict.create_task("T-9", "Empty plan")
    strict.transition("T-9", TaskState.PLAN)  # empty scaffold → execute blocked

    with pytest.raises(EmptyPlanError):
        strict.transition("T-9", TaskState.EXECUTE, final_state="should not persist")

    # The raise fired before _save_task — the card on disk is untouched.
    reloaded = strict.get_task("T-9")
    assert reloaded.final_state == ""
    assert reloaded.state == TaskState.PLAN


def test_state_md_lists_all_tasks(mgr):
    mgr.create_task("T-1", "First task", priority="high")
    mgr.create_task("T-2", "Second task")

    assert mgr.state_md_path.exists()
    content = mgr.state_md_path.read_text()
    assert "T-1" in content
    assert "T-2" in content
    assert "[high]" in content
    assert "BRAINSTORM" in content


def test_state_md_shows_priority(mgr):
    mgr.create_task("T-1", "Important", priority="high")

    content = mgr.state_md_path.read_text()
    assert "[high]" in content


def test_sync(mgr):
    mgr.create_task("T-1", "Task 1")
    mgr.create_task("T-2", "Task 2")

    mgr.state_md_path.unlink()
    assert not mgr.state_md_path.exists()

    count = mgr.sync()
    assert count == 2
    assert mgr.state_md_path.exists()


def test_sync_removes_legacy_backlog_md(mgr):
    """Migration: stale backlog.md from prior versions is cleaned up on sync."""
    mgr.create_task("T-1", "Task 1")
    legacy = mgr.project_dir / ".agent" / "backlog.md"
    legacy.write_text("# stale content from old version\n")
    assert legacy.exists()

    mgr.sync()
    assert not legacy.exists()


def test_tags_on_task(mgr):
    t, _ = mgr.create_task("T-1", "Tagged task", tags=["p0", "mvp"])
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
    mgr.transition("T-1", TaskState.DOCUMENT)

    mgr.log_work("T-1", "Docs updated")
    mgr.transition("T-1", TaskState.REVIEW, final_state="Feature implemented and tested")

    mgr.log_work("T-1", "Review passed")
    mgr.transition("T-1", TaskState.DONE)

    t = mgr.get_task("T-1")
    assert t.state == TaskState.DONE
    assert t.completed_at != ""
    assert t.final_state == "Feature implemented and tested"
    assert len(t.work_log) == 5


# -- Update tests --


def test_update_priority(mgr):
    mgr.create_task("T-1", "Update me")
    t, _ = mgr.update_task("T-1", priority="high")
    assert t.priority == "high"
    # Persists
    t = mgr.get_task("T-1")
    assert t.priority == "high"


def test_update_description_and_title(mgr):
    mgr.create_task("T-1", "Old title", description="old desc")
    t, _ = mgr.update_task("T-1", title="New title", description="new desc")
    assert t.title == "New title"
    assert t.description == "new desc"


def test_update_resolution(mgr):
    mgr.create_task("T-1", "Close me")
    t, _ = mgr.update_task("T-1", resolution="Closed: duplicate of T-2")
    assert t.resolution == "Closed: duplicate of T-2"


def test_update_tags(mgr):
    mgr.create_task("T-1", "Tag me", tags=["a", "b"])
    t, _ = mgr.update_task("T-1", add_tags=["c"], remove_tags=["a"])
    assert "c" in t.tags
    assert "a" not in t.tags
    assert "b" in t.tags


def test_update_nonexistent_task(mgr):
    with pytest.raises(ValueError, match="not found"):
        mgr.update_task("NOPE", priority="high")


# -- Relationships: parent_task / depends_on (HATS-198) --


def test_create_with_parent_and_depends(mgr):
    mgr.create_task("T-0", "Epic")
    mgr.create_task("T-9", "Blocker")
    t, _ = mgr.create_task("T-1", "Child", parent_task="T-0", depends_on=["T-9"])
    assert t.parent_task == "T-0"
    assert t.depends_on == ["T-9"]
    # Persists
    reloaded = mgr.get_task("T-1")
    assert reloaded.parent_task == "T-0"
    assert reloaded.depends_on == ["T-9"]


def test_update_set_and_clear_parent(mgr):
    mgr.create_task("T-0", "Epic")
    mgr.create_task("T-1", "Child")
    t, _ = mgr.update_task("T-1", parent_task="T-0")
    assert t.parent_task == "T-0"
    t, _ = mgr.update_task("T-1", parent_task="")
    assert t.parent_task == ""


def test_update_add_and_remove_depends(mgr):
    mgr.create_task("T-9", "Blocker A")
    mgr.create_task("T-8", "Blocker B")
    mgr.create_task("T-7", "Blocker C")
    mgr.create_task("T-1", "Blocked", depends_on=["T-9", "T-8"])
    t, _ = mgr.update_task("T-1", add_depends=["T-7"], remove_depends=["T-9"])
    assert "T-7" in t.depends_on
    assert "T-9" not in t.depends_on
    assert "T-8" in t.depends_on


def test_self_reference_rejected_in_create(mgr):
    with pytest.raises(ValueError, match="own parent"):
        mgr.create_task("T-1", "Self-parent", parent_task="T-1")
    with pytest.raises(ValueError, match="depend on itself"):
        mgr.create_task("T-2", "Self-depends", depends_on=["T-2"])


def test_self_reference_rejected_in_update(mgr):
    mgr.create_task("T-1", "Sample")
    with pytest.raises(ValueError, match="own parent"):
        mgr.update_task("T-1", parent_task="T-1")
    with pytest.raises(ValueError, match="depend on itself"):
        mgr.update_task("T-1", add_depends=["T-1"])


def test_simple_cycle_rejected(mgr):
    """A.depends_on=[B] is fine. Then B.depends_on=[A] must reject."""
    mgr.create_task("T-A", "A")
    mgr.create_task("T-B", "B", depends_on=["T-A"])
    with pytest.raises(ValueError, match="Cycle"):
        mgr.update_task("T-A", add_depends=["T-B"])


def test_missing_refs_returns_unknown_ids(mgr):
    """Manager-level missing_refs is a pure read — no warnings, just diagnostics."""
    mgr.create_task("T-1", "Real")
    assert mgr.missing_refs(["T-1", "T-99", "T-42"]) == ["T-99", "T-42"]
    assert mgr.missing_refs([]) == []
    assert mgr.missing_refs(["T-1"]) == []


def test_create_does_not_block_on_missing_refs(mgr):
    """Forward-references and typos must NOT block writes — just be reported."""
    t, _ = mgr.create_task("T-1", "Forward ref", parent_task="T-NOT-YET", depends_on=["T-99"])
    assert t.parent_task == "T-NOT-YET"
    assert t.depends_on == ["T-99"]
    # And missing_refs reports them so the CLI can warn.
    assert set(mgr.missing_refs(["T-NOT-YET", "T-99"])) == {"T-NOT-YET", "T-99"}


def test_depends_round_trip_through_yaml(mgr):
    mgr.create_task("T-9", "Dep")
    mgr.create_task("T-1", "Has deps", parent_task="", depends_on=["T-9"])
    # Force reload from disk to catch serialization regressions.
    reloaded = mgr.get_task("T-1")
    assert reloaded.depends_on == ["T-9"]


def test_empty_depends_does_not_appear_in_yaml(mgr):
    """Cards without blockers must NOT grow a `depends_on: []` line on save —
    keeps pre-HATS-198 backlogs byte-clean and avoids cosmetic diff churn."""
    mgr.create_task("T-1", "No deps")
    yaml_text = (mgr.tasks_dir / "T-1" / "task.yaml").read_text()
    assert "depends_on" not in yaml_text

    mgr.update_task("T-1", priority="high")
    yaml_text = (mgr.tasks_dir / "T-1" / "task.yaml").read_text()
    assert "depends_on" not in yaml_text


def test_non_empty_depends_appears_in_yaml(mgr):
    """Symmetric guard: when depends_on is set, it MUST serialize."""
    mgr.create_task("T-9", "Blocker")
    mgr.create_task("T-1", "Blocked", depends_on=["T-9"])
    yaml_text = (mgr.tasks_dir / "T-1" / "task.yaml").read_text()
    assert "depends_on" in yaml_text
    assert "T-9" in yaml_text


def test_legacy_yaml_without_depends_loads(mgr, tmp_path):
    """Cards written before HATS-198 don't have a `depends_on:` key.
    They must load with depends_on == [] (default), not crash."""
    legacy = mgr.tasks_dir / "T-OLD"
    legacy.mkdir(parents=True)
    (legacy / "task.yaml").write_text(
        "id: T-OLD\n"
        "title: Pre-HATS-198 card\n"
        "state: brainstorm\n"
        "priority: medium\n"
        "parent_task: ''\n"
        "tags: []\n"
        "created: '2026-01-01T00:00:00Z'\n"
        "updated: '2026-01-01T00:00:00Z'\n"
    )
    t = mgr.get_task("T-OLD")
    assert t is not None
    assert t.depends_on == []
    # And the round trip still doesn't lose anything.
    mgr.update_task("T-OLD", priority="high")
    t2 = mgr.get_task("T-OLD")
    assert t2.depends_on == []
    assert t2.priority == "high"


# -- Worktree integration tests --


def _init_git(path: Path) -> None:
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True
    )
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
    return TaskManager(project, strict_plan_check=False)


def test_execute_creates_worktree(git_mgr):
    git_mgr.create_task("T-1", "Work in worktree")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert active is not None
    assert active.branch_name == "task/t-1"
    assert active.worktree_path.exists()


def test_done_merges_worktree(git_mgr):
    git_mgr.create_task("T-1", "Merge on done")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    # Make a change in the worktree
    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    (active.worktree_path / "new_file.txt").write_text("hello")
    subprocess.run(
        ["git", "add", "."], cwd=str(active.worktree_path), capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "add file"],
        cwd=str(active.worktree_path),
        capture_output=True,
        check=True,
    )

    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)
    git_mgr.transition("T-1", TaskState.DONE)

    # Worktree cleaned up
    assert (
        WorktreeManager.load_for_task(
            git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
        )
        is None
    )
    # Change merged into main
    assert (git_mgr.project_dir / "new_file.txt").exists()


def test_failed_discards_worktree(git_mgr):
    git_mgr.create_task("T-1", "Fail and discard")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    wt_path = active.worktree_path
    assert wt_path.exists()

    git_mgr.transition("T-1", TaskState.FAILED)

    # Worktree removed
    assert (
        WorktreeManager.load_for_task(
            git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
        )
        is None
    )
    assert not wt_path.exists()


def test_parallel_tasks_get_independent_worktrees(git_mgr):
    """HATS-061: two tasks in execute → two independent worktrees."""
    git_mgr.create_task("T-1", "First task")
    git_mgr.create_task("T-2", "Second task")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-2", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)
    git_mgr.transition("T-2", TaskState.EXECUTE)

    wt1 = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    wt2 = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-2", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert wt1 is not None
    assert wt2 is not None
    assert wt1.worktree_path != wt2.worktree_path
    assert wt1.branch_name == "task/t-1"
    assert wt2.branch_name == "task/t-2"

    # list_active returns both
    active = WorktreeManager.list_active(
        git_mgr.project_dir, state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert len(active) == 2


def test_done_merges_only_its_own_worktree(git_mgr):
    """HATS-061: done on task A leaves task B's worktree untouched."""
    git_mgr.create_task("T-1", "Task A")
    git_mgr.create_task("T-2", "Task B")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-2", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)
    git_mgr.transition("T-2", TaskState.EXECUTE)

    # Make a change in T-1's worktree
    wt1 = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    (wt1.worktree_path / "from_t1.txt").write_text("t1")
    subprocess.run(["git", "add", "."], cwd=str(wt1.worktree_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "t1 work"],
        cwd=str(wt1.worktree_path),
        capture_output=True,
        check=True,
    )

    # Complete T-1
    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)
    git_mgr.transition("T-1", TaskState.DONE)

    # T-1 merged, T-2 still active
    assert (
        WorktreeManager.load_for_task(
            git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
        )
        is None
    )
    assert (git_mgr.project_dir / "from_t1.txt").exists()

    wt2 = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-2", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert wt2 is not None
    assert wt2.worktree_path.exists()


def test_execute_reuses_worktree_after_blocked(git_mgr):
    git_mgr.create_task("T-1", "Block and resume")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    wt_path = active.worktree_path

    git_mgr.transition("T-1", TaskState.BLOCKED)
    # Worktree still active after blocked
    assert (
        WorktreeManager.load_for_task(
            git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
        )
        is not None
    )

    git_mgr.transition("T-1", TaskState.EXECUTE)
    # Same worktree reused
    active2 = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert active2.worktree_path == wt_path


def test_no_worktree_in_non_git_project(mgr):
    """Non-git projects skip worktree creation silently."""
    mgr.create_task("T-1", "No git here")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_for_task(mgr.project_dir, "T-1")
    assert active is None


# -- Cancelled / wont-fix terminal state (HATS-168) --


def test_cancel_from_brainstorm_is_terminal(mgr):
    """Fresh card → cancelled with resolution recorded; completed_at stamped."""
    mgr.create_task("T-1", "Drop this")
    t, _ = mgr.transition("T-1", TaskState.CANCELLED, resolution="duplicate of T-99")

    assert t.state == TaskState.CANCELLED
    assert t.resolution == "duplicate of T-99"
    assert t.completed_at != ""

    # Reload from disk — resolution and state must persist.
    reloaded = mgr.get_task("T-1")
    assert reloaded.state == TaskState.CANCELLED
    assert reloaded.resolution == "duplicate of T-99"


@pytest.mark.parametrize(
    "path",
    [
        [TaskState.PLAN],
        [TaskState.PLAN, TaskState.BLOCKED],
        [TaskState.PLAN, TaskState.EXECUTE],
        [TaskState.PLAN, TaskState.EXECUTE, TaskState.DOCUMENT],
        [TaskState.PLAN, TaskState.EXECUTE, TaskState.DOCUMENT, TaskState.REVIEW],
        [TaskState.PLAN, TaskState.EXECUTE, TaskState.FAILED],
    ],
    ids=["plan", "blocked", "execute", "document", "review", "failed"],
)
def test_cancel_reachable_from_every_non_terminal_state(mgr, path):
    """Cancel exit is the whole point of the feature — must work from anywhere."""
    mgr.create_task("T-1", "Walk before cancel")
    for state in path:
        mgr.transition("T-1", state)

    t, _ = mgr.transition("T-1", TaskState.CANCELLED, resolution="obsolete")
    assert t.state == TaskState.CANCELLED
    assert t.resolution == "obsolete"
    assert t.completed_at != ""


def test_cancelled_is_terminal(mgr):
    """Once cancelled, no further transitions are valid."""
    mgr.create_task("T-1", "Terminal check")
    mgr.transition("T-1", TaskState.CANCELLED, resolution="closed")

    for target in TaskState:
        if target == TaskState.CANCELLED:
            continue
        with pytest.raises(ValueError, match="Invalid transition"):
            mgr.transition("T-1", target)


def test_cancel_resolution_optional_at_manager_level(mgr):
    """TaskManager itself doesn't enforce --resolution — that's CLI policy.

    Keeps the manager API permissive (single source of truth for validation
    sits at the user-facing edge, not duplicated in two places).
    """
    mgr.create_task("T-1", "No resolution here")
    t, _ = mgr.transition("T-1", TaskState.CANCELLED)  # no resolution kwarg
    assert t.state == TaskState.CANCELLED
    assert t.resolution == ""


def test_cancel_appears_in_state_md(mgr):
    mgr.create_task("T-1", "Show me in STATE.md")
    mgr.transition("T-1", TaskState.CANCELLED, resolution="dup")

    content = mgr.state_md_path.read_text()
    assert "## CANCELLED" in content
    assert "T-1" in content


def test_cancel_discards_worktree(git_mgr):
    """plan→execute→cancelled: worktree torn down, changes NOT merged into main."""
    git_mgr.create_task("T-1", "Work then drop")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)

    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert active is not None
    wt_path = active.worktree_path

    # Make + commit a change inside the worktree to prove it's discarded.
    (wt_path / "junk.txt").write_text("should not survive")
    subprocess.run(["git", "add", "."], cwd=str(wt_path), capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "wip junk"], cwd=str(wt_path), capture_output=True, check=True
    )

    git_mgr.transition("T-1", TaskState.CANCELLED, resolution="wont-fix per review")

    # Worktree dir gone, state slot cleared.
    assert (
        WorktreeManager.load_for_task(
            git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
        )
        is None
    )
    assert not wt_path.exists()
    # Change NOT merged into main (cancelled is not done).
    assert not (git_mgr.project_dir / "junk.txt").exists()

    # Card finalized correctly.
    t = git_mgr.get_task("T-1")
    assert t.state == TaskState.CANCELLED
    assert t.resolution == "wont-fix per review"
    assert t.completed_at != ""


def test_execute_inside_linked_worktree_does_not_nest(tmp_path):
    """HATS-060 / HATS-840: `task transition execute` from inside a linked worktree
    adopts it and must NOT create a second nested worktree. The manager runs against
    MAIN (where `_project_dir()` hops to, HATS-524) and the operator's cwd is threaded
    as `caller_cwd` (HATS-840); MAIN carries the `.agent/` marker, so the HATS-839
    write-gate passes.
    """
    project = tmp_path / "project"
    project.mkdir()
    _init_git(project)
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")

    # Step 1: replicate `ai-hats wt create feat/T-1-foo` from main.
    wt = WorktreeManager(project, branch_name="feat/T-1-foo")
    wt_path = wt.create()
    wt.save_state()

    # Step 2: create + plan the task (the CLI does this from MAIN).
    mgr = TaskManager(project, strict_plan_check=False)
    mgr.create_task("T-1", "Created from main, executed from inside a worktree")
    mgr.transition("T-1", TaskState.PLAN)

    # Step 3: execute while the operator's shell is inside the linked worktree.
    mgr.transition("T-1", TaskState.EXECUTE, caller_cwd=wt_path)

    # Assertion 1: no second worktree created on task/t-1.
    wts = WorktreeManager.list_worktrees(project)
    branches = {w.get("branch", "") for w in wts}
    assert "task/t-1" not in branches, f"Nested worktree created: {branches}"

    # Assertion 2: state file in main repo still points at the original.
    active = WorktreeManager.load_for_branch(project, "feat/T-1-foo")
    assert active is not None
    assert active.branch_name == "feat/T-1-foo"
    assert active.worktree_path == wt_path

    # Assertion 3: no stray state files inside the linked worktree.
    assert not (wt_path / ".agent" / "worktree.json").exists()
    assert not (wt_path / ".agent" / "worktrees").exists()


def test_execute_from_hopped_main_adopts_via_caller_cwd(tmp_path):
    """HATS-840: `transition execute` issued from inside a linked worktree adopts
    it even when the TaskManager's ``project_dir`` is the main-hopped checkout —
    the real CLI case, where ``_project_dir()`` hops a worktree to MAIN (HATS-524).
    The adopt signal is the explicitly-threaded ``caller_cwd``, NOT
    ``self.project_dir`` (which is MAIN here, so the old check never fired).

    Complements ``test_execute_inside_linked_worktree_does_not_nest``, which builds
    ``TaskManager(wt_path)`` and so cannot exercise the hop that triggers the bug.
    """
    project = tmp_path / "project"
    project.mkdir()
    _init_git(project)
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")

    # Operator's worktree, created from main (mirrors `ai-hats wt create`).
    wt = WorktreeManager(project, branch_name="feat/T-9-foo")
    wt_path = wt.create()
    wt.save_state()

    # TaskManager is constructed with MAIN — the value `_project_dir()` hops to
    # when the operator's shell is inside the worktree.
    main_mgr = TaskManager(project, strict_plan_check=False)
    main_mgr.create_task("T-9", "Created from main, executed from inside a worktree")
    main_mgr.transition("T-9", TaskState.PLAN)

    # Execute fires while the operator's shell is inside the linked worktree.
    main_mgr.transition("T-9", TaskState.EXECUTE, caller_cwd=wt_path)

    # No second worktree on task/t-9 — the caller's worktree was adopted.
    branches = {w.get("branch", "") for w in WorktreeManager.list_worktrees(project)}
    assert "task/t-9" not in branches, f"Nested worktree created: {branches}"


# -- HATS-371: linking, fast-close, force-transition ----------------------


def test_close_from_brainstorm_sets_done(mgr):
    mgr.create_task("T-1", "Shipped on master")
    t, _ = mgr.close_task("T-1", "shipped in 6e7ddd5")
    assert t.state == TaskState.DONE
    assert t.resolution == "shipped in 6e7ddd5"
    assert t.completed_at != ""
    assert any("Fast-closed from brainstorm" in e.message for e in t.work_log)


def test_close_from_plan_sets_done(mgr):
    mgr.create_task("T-1", "Plan then fast-close")
    mgr.transition("T-1", TaskState.PLAN)
    t, _ = mgr.close_task("T-1", "subsumed by T-2")
    assert t.state == TaskState.DONE
    assert any("Fast-closed from plan" in e.message for e in t.work_log)


def test_close_rejects_from_execute(mgr):
    """Fast-close only makes sense from brainstorm/plan — execute has a worktree."""
    mgr.create_task("T-1", "Has worktree")
    mgr.transition("T-1", TaskState.PLAN)
    # Force into execute without strict plan check (fixture sets it false anyway).
    mgr.transition("T-1", TaskState.EXECUTE)
    with pytest.raises(ValueError, match="brainstorm or plan"):
        mgr.close_task("T-1", "nope")


def test_close_requires_resolution(mgr):
    mgr.create_task("T-1", "needs reason")
    with pytest.raises(ValueError, match="non-empty resolution"):
        mgr.close_task("T-1", "")


def test_close_yaml_byte_clean_for_unrelated_fields(mgr):
    """Closing a task must not introduce empty link fields into YAML."""
    mgr.create_task("T-1", "fast close")
    mgr.close_task("T-1", "done")
    yaml_text = (mgr.tasks_dir / "T-1" / "task.yaml").read_text()
    assert "related:" not in yaml_text
    assert "see_also:" not in yaml_text
    assert "folded_into:" not in yaml_text


def test_force_transition_skips_guard(mgr):
    """plan → brainstorm is normally invalid; --force allows it."""
    mgr.create_task("T-1", "rollback me")
    mgr.transition("T-1", TaskState.PLAN)
    assert mgr.get_task("T-1").state == TaskState.PLAN
    t, _ = mgr.transition("T-1", TaskState.BRAINSTORM, force=True, reason="plan started by mistake")
    assert t.state == TaskState.BRAINSTORM
    assert any("Forced transition plan → brainstorm" in e.message for e in t.work_log)


def test_force_requires_reason(mgr):
    mgr.create_task("T-1", "need reason")
    mgr.transition("T-1", TaskState.PLAN)
    with pytest.raises(ValueError, match="reason"):
        mgr.transition("T-1", TaskState.BRAINSTORM, force=True, reason="")


def test_force_same_state_rejected(mgr):
    mgr.create_task("T-1", "already there")
    with pytest.raises(ValueError, match="already in state"):
        mgr.transition("T-1", TaskState.BRAINSTORM, force=True, reason="oops")


def test_add_link_related_is_bidirectional(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2", link_type="related")
    a = mgr.get_task("T-1")
    b = mgr.get_task("T-2")
    assert a.related == ["T-2"]
    assert b.related == ["T-1"]


def test_add_link_see_also_is_bidirectional(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2", link_type="see-also")
    assert mgr.get_task("T-1").see_also == ["T-2"]
    assert mgr.get_task("T-2").see_also == ["T-1"]


def test_add_link_fold_is_directional(mgr):
    mgr.create_task("T-1", "Folded")
    mgr.create_task("T-2", "Keeper")
    mgr.add_link("T-1", "T-2", link_type="fold")
    assert mgr.get_task("T-1").folded_into == "T-2"
    assert mgr.get_task("T-2").folded_into == ""


def test_add_link_fold_refuses_overwrite(mgr):
    mgr.create_task("T-1", "Folded")
    mgr.create_task("T-2", "First keeper")
    mgr.create_task("T-3", "Second keeper")
    mgr.add_link("T-1", "T-2", link_type="fold")
    with pytest.raises(ValueError, match="already folded into"):
        mgr.add_link("T-1", "T-3", link_type="fold")


def test_add_link_self_rejected(mgr):
    mgr.create_task("T-1", "A")
    with pytest.raises(ValueError, match="link a task to itself"):
        mgr.add_link("T-1", "T-1")


def test_add_link_unknown_type_rejected(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    with pytest.raises(ValueError, match="Unknown link type"):
        mgr.add_link("T-1", "T-2", link_type="bogus")


def test_add_link_unknown_target_rejected(mgr):
    mgr.create_task("T-1", "A")
    with pytest.raises(ValueError, match="not found"):
        mgr.add_link("T-1", "T-99")


def test_add_link_idempotent(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2")
    mgr.add_link("T-1", "T-2")  # second call: must not duplicate
    assert mgr.get_task("T-1").related == ["T-2"]


def test_remove_link_related(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2")
    mgr.remove_link("T-1", "T-2")
    assert mgr.get_task("T-1").related == []
    assert mgr.get_task("T-2").related == []


def test_remove_link_fold(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2", link_type="fold")
    mgr.remove_link("T-1", "T-2", link_type="fold")
    assert mgr.get_task("T-1").folded_into == ""


def test_remove_link_no_op_on_absent(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    # Never linked — must not raise.
    mgr.remove_link("T-1", "T-2")
    assert mgr.get_task("T-1").related == []


def test_find_subsumed_by(mgr):
    mgr.create_task("T-1", "Folded into 3")
    mgr.create_task("T-2", "Folded into 3 also")
    mgr.create_task("T-3", "Keeper")
    mgr.add_link("T-1", "T-3", link_type="fold")
    mgr.add_link("T-2", "T-3", link_type="fold")
    subsumed = mgr.find_subsumed_by("T-3")
    assert set(subsumed) == {"T-1", "T-2"}


def test_find_subsumed_by_empty_when_none(mgr):
    mgr.create_task("T-1", "Lonely")
    assert mgr.find_subsumed_by("T-1") == []


def test_link_fields_byte_clean_when_empty(mgr):
    mgr.create_task("T-1", "No links")
    yaml_text = (mgr.tasks_dir / "T-1" / "task.yaml").read_text()
    assert "related" not in yaml_text
    assert "see_also" not in yaml_text
    assert "folded_into" not in yaml_text


def test_link_round_trip_through_yaml(mgr):
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2", link_type="related")
    mgr.add_link("T-1", "T-2", link_type="see-also")
    # Reload from disk and verify the fields stuck.
    a = mgr.get_task("T-1")
    assert a.related == ["T-2"]
    assert a.see_also == ["T-2"]


# ---------------------------------------------------------------------------
# HATS-481 — L4' fail-loud teardown
# ---------------------------------------------------------------------------


class _FailingMergeManager:
    """Stand-in for WorktreeManager: merge() raises CalledProcessError."""

    def __init__(self, branch_name: str = "task/t-1") -> None:
        self.branch_name = branch_name

    def merge(self, *, force: bool = False) -> None:
        raise subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "merge", "--no-ff", self.branch_name],
            stderr=(
                "fatal: Unable to create '.git/index.lock': File exists.\n"
                "Another git process seems to be running in this repository."
            ),
        )

    def discard(self, *, force: bool = False) -> None:  # pragma: no cover
        pass


def test_teardown_worktree_reraises_on_merge_failure(mgr, monkeypatch):
    """TC-N13 (HATS-481 L4'): merge failure on ``transition done`` must
    leave the task in ``review`` — not silently mark it DONE.

    Pre-HATS-481 the ``except Exception`` block in ``_teardown_worktree``
    swallowed every error at WARNING, then ``transition`` proceeded to
    ``_save_task``, persisting the new DONE state despite the merge
    failure. That's the silent-data-loss class (GH Merge Queue
    Apr-2026 postmortem). L4' re-raises → ``_save_task`` is never
    reached under the filelock context manager → on-disk state stays
    at ``review``.
    """
    from ai_hats import wt as worktree_module

    mgr.create_task("T-1", "L4' regression coverage")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)
    mgr.transition("T-1", TaskState.DOCUMENT)
    mgr.transition("T-1", TaskState.REVIEW)

    # Force `_teardown_worktree(merge=True)` to see a manager whose
    # `.merge()` raises. The real `load_for_task` would return None for
    # this non-git fixture project, so without the patch the teardown
    # is a no-op and the bug is not reproducible.
    def fake_load_for_task(project_dir, task_id, *, lifecycle=None, state_dir=None):
        return _FailingMergeManager(branch_name=f"task/{task_id.lower()}")

    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(fake_load_for_task),
    )

    with pytest.raises(subprocess.CalledProcessError):
        mgr.transition("T-1", TaskState.DONE)

    # The on-disk task must NOT have moved to DONE.
    reloaded = mgr.get_task("T-1")
    assert reloaded.state == TaskState.REVIEW, (
        f"task moved to {reloaded.state} despite merge failure — silent data loss regression"
    )
    assert reloaded.completed_at == "", "completed_at must not be persisted when merge fails"


def test_done_force_forwards_to_merge(mgr, monkeypatch):
    """HATS-596: ``transition done --force`` must forward ``force=True`` into
    ``Worktree.merge()`` so a corrective override reaches the merge guards.

    Pre-596 ``_teardown_worktree`` called ``active.merge()`` with no args, so
    ``--force`` never reached the merge — the git-integration check could not
    be overridden at all.
    """
    from ai_hats import wt as worktree_module

    captured: dict[str, bool] = {}

    class _SpyManager:
        branch_name = "task/t-1"

        def merge(self, *, force: bool = False) -> None:
            captured["force"] = force

        def discard(self, *, force: bool = False) -> None:  # pragma: no cover
            pass

    mgr.create_task("T-1", "force plumbing")
    mgr.transition("T-1", TaskState.PLAN)
    mgr.transition("T-1", TaskState.EXECUTE)
    mgr.transition("T-1", TaskState.DOCUMENT)
    mgr.transition("T-1", TaskState.REVIEW)

    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: _SpyManager()),
    )

    mgr.transition("T-1", TaskState.DONE, force=True, reason="corrective finalize")
    assert captured.get("force") is True, (
        "transition done --force must forward force=True to Worktree.merge()"
    )


def test_teardown_worktree_swallows_discard_failure(mgr, monkeypatch):
    """L4' must not regress discard semantics: ``transition failed`` /
    ``transition cancelled`` (merge=False) keep the swallowing behavior,
    because the user is administratively dropping the work."""
    from ai_hats import wt as worktree_module

    class _FailingDiscardManager:
        branch_name = "task/t-2"

        def merge(self, *, force: bool = False):  # pragma: no cover
            pass

        def discard(self, *, force: bool = False):
            raise subprocess.CalledProcessError(
                returncode=128,
                cmd=["git", "branch", "-D"],
                stderr="fatal: could not lock something",
            )

    mgr.create_task("T-2", "Cancel me")
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: _FailingDiscardManager()),
    )
    # Should NOT raise — admin close path is permissive.
    mgr.transition("T-2", TaskState.CANCELLED, resolution="dropped")

    reloaded = mgr.get_task("T-2")
    assert reloaded.state == TaskState.CANCELLED


# ---------------------------------------------------------------------------
# HATS-541 — defensive raise when worktree state is lost mid-lifecycle
# ---------------------------------------------------------------------------


def test_teardown_worktree_raises_when_state_lost_but_branch_exists(git_mgr, monkeypatch):
    """HATS-541: a ``transition done`` whose worktree state JSON is gone
    while the branch still exists must NOT silently mark the task DONE.

    The orphan state — ``load_for_task`` returns ``None`` but the
    ``task/<id>`` branch persists — must trigger ``WorktreeStateLostError``
    so ``_save_task`` never stamps DONE without a merge (same
    silent-data-loss class as HATS-481).

    HATS-587 note: post-F5 a *failed* merge no longer produces this orphan
    (the worktree dir + state + branch are all preserved for a clean
    retry). This test exercises the guard as **defense-in-depth** for the
    residual causes (manual state deletion, crash on the success path,
    pre-587 orphans) by stubbing the orphan directly: ``load_for_task``
    returns ``None`` on a real git repo where ``branch_exists("task/t-1")``
    is True. The first stub (failing merge → task stays REVIEW) still
    pins the HATS-481 fail-loud contract.

    HATS-697: the branch must carry genuinely un-merged commits for this
    guard to fire — the new ancestry-aware short-circuit finalizes an
    already-merged orphan instead (see
    ``test_teardown_worktree_finalizes_when_state_lost_but_branch_merged``).
    """
    from ai_hats import wt as worktree_module
    from ai_hats.wt import WorktreeStateLostError

    git_mgr.create_task("T-1", "HATS-541 regression")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)  # creates task/t-1 branch

    # HATS-697: the guard protects genuinely UN-MERGED work, so the orphan
    # branch must actually diverge from the base. Commit a file on the
    # worktree branch (NOT merged anywhere) — otherwise `task/t-1` would be
    # created at base HEAD and the new ancestry-aware short-circuit would
    # (correctly) finalize it without a re-merge instead of refusing.
    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert active is not None and active.worktree_path is not None
    (active.worktree_path / "unmerged.txt").write_text("un-merged work\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(active.worktree_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "un-merged worktree work"],
        cwd=str(active.worktree_path),
        capture_output=True,
        check=True,
    )

    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)

    # Attempt 1: load_for_task returns a manager whose merge() raises.
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: _FailingMergeManager(branch_name="task/t-1")),
    )
    with pytest.raises(subprocess.CalledProcessError):
        git_mgr.transition("T-1", TaskState.DONE)
    assert git_mgr.get_task("T-1").state == TaskState.REVIEW

    # Attempt 2: state.json gone — load_for_task returns None.
    # Branch is still there (we never deleted it in this test stub) so
    # branch_exists returns True, and HATS-541's defensive raise fires.
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: None),
    )
    with pytest.raises(WorktreeStateLostError) as exc_info:
        git_mgr.transition("T-1", TaskState.DONE)

    # Exception carries the data the CLI handler needs.
    assert exc_info.value.task_id == "T-1"
    assert exc_info.value.branch_name == "task/t-1"

    # Task must still be in REVIEW (NOT silently bumped to DONE).
    reloaded = git_mgr.get_task("T-1")
    assert reloaded.state == TaskState.REVIEW, (
        f"task moved to {reloaded.state} despite worktree state loss — "
        f"HATS-541 silent-data-loss regression"
    )
    assert reloaded.completed_at == "", (
        "completed_at must not be persisted when the merge never happened"
    )


def test_teardown_worktree_finalizes_when_state_lost_but_branch_merged(git_mgr, monkeypatch):
    """HATS-697: state lost + branch ALREADY merged → finalize, don't refuse.

    The shipped-on-master / removed-worktree scenario from PROX-287: the
    ``task/<id>`` work was integrated out-of-band (manual ``git merge
    --no-ff``) and/or the auto-worktree was removed by hand, leaving
    ``load_for_task`` → ``None``. Re-merging is a no-op; the old guard
    raised a FALSE ``WorktreeStateLostError`` ("un-merged commits"). The
    ancestry-aware short-circuit must instead mark the task DONE and clean
    up the now-merged branch.

    Fail-under-revert: drop the
    ``branch_merged_into_canonical_base`` check in ``_teardown_worktree``
    (raise unconditionally when the branch exists) → this test sees
    ``WorktreeStateLostError`` and the ``state == DONE`` assertion fails.
    """
    from ai_hats import wt as worktree_module

    git_mgr.create_task("T-1", "HATS-697 already-merged finalize")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)  # creates task/t-1 branch

    # Commit work on the worktree branch, then merge it into the base in the
    # main repo (out-of-band) — exactly the manual-ship the FSM must tolerate.
    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    assert active is not None and active.worktree_path is not None
    wt_path = active.worktree_path
    (wt_path / "shipped.txt").write_text("shipped on master\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(wt_path),
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "shipped work"],
        cwd=str(wt_path),
        capture_output=True,
        check=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(git_mgr.project_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-edit", "task/t-1"],
        cwd=str(git_mgr.project_dir),
        capture_output=True,
        check=True,
    )
    base_sha_after_merge = subprocess.run(
        ["git", "rev-parse", base],
        cwd=str(git_mgr.project_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)

    # Reproduce the bug trigger: the auto-worktree was removed by hand,
    # freeing the (already-merged) branch, and the state JSON is gone.
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=str(git_mgr.project_dir),
        capture_output=True,
        check=True,
    )
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: None),
    )

    # Must NOT raise — the work is integrated, so finalize without re-merge.
    git_mgr.transition("T-1", TaskState.DONE)

    reloaded = git_mgr.get_task("T-1")
    assert reloaded.state == TaskState.DONE, (
        f"already-merged orphan should finalize, got {reloaded.state}"
    )
    assert reloaded.completed_at != "", "completed_at must be stamped on DONE"
    # The now-merged branch was cleaned up (best-effort safe delete).
    assert not WorktreeManager.branch_exists(git_mgr.project_dir, "task/t-1"), (
        "merged branch should be deleted by the finalize short-circuit"
    )
    # No double-merge: the base ref is unchanged since the manual merge.
    base_sha_now = subprocess.run(
        ["git", "rev-parse", base],
        cwd=str(git_mgr.project_dir),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert base_sha_now == base_sha_after_merge, (
        "base ref moved — the short-circuit must NOT run a second git merge"
    )


def test_teardown_worktree_silent_when_state_and_branch_both_gone(git_mgr, monkeypatch):
    """HATS-541 carve-out: when both ``state.json`` AND the
    ``task/<id>`` branch are absent, ``transition done`` is a
    legitimate admin no-op — silently complete.

    Distinguishes the silent-data-loss case (branch preserved, state
    lost) from the truly-empty case (nothing ever existed, or both
    were intentionally cleaned). The latter must remain permissive
    because admin closes via ``task close`` and similar paths legitimately
    reach this branch with no underlying worktree state.
    """
    from ai_hats import wt as worktree_module

    # Walk through review on the real git_mgr fixture, then drop the
    # worktree + branch — mimicking the "no worktree state, branch
    # already cleaned" admin-close starting condition. Direct
    # subprocess + WorktreeManager teardown so we don't touch the
    # TaskManager state (which would mark task DONE prematurely).
    git_mgr.create_task("T-1", "no worktree branch surviving")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)
    git_mgr.transition("T-1", TaskState.DOCUMENT)
    git_mgr.transition("T-1", TaskState.REVIEW)

    # Force-discard the worktree (removes dir, deletes branch, clears
    # state) so the carve-out's preconditions hold: no state, no
    # branch. Matches what `task transition failed` would have done.
    active = WorktreeManager.load_for_task(
        git_mgr.project_dir, "T-1", state_dir=worktrees_dir(git_mgr.project_dir)
    )
    if active is not None:
        active.discard(force=True)

    # Sanity: branch is gone.
    assert not WorktreeManager.branch_exists(git_mgr.project_dir, "task/t-1")

    # Stub load_for_task to return None — state is gone too.
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: None),
    )

    # MUST NOT raise — branch doesn't exist, no recovery is needed.
    git_mgr.transition("T-1", TaskState.DONE)
    assert git_mgr.get_task("T-1").state == TaskState.DONE


def test_teardown_worktree_silent_on_discard_path_when_state_lost(git_mgr, monkeypatch):
    """HATS-541 carve-out: the ``merge=False`` discard path
    (``transition failed`` / ``transition cancelled``) keeps silent
    return even when the branch still exists.

    Discard is intentionally lossy by design — refusing it would
    block admin closes. The defensive raise applies only to the
    ``merge=True`` DONE path where silent success would be data loss.
    """
    from ai_hats import wt as worktree_module

    git_mgr.create_task("T-1", "discard with orphan branch")
    git_mgr.transition("T-1", TaskState.PLAN)
    git_mgr.transition("T-1", TaskState.EXECUTE)  # creates task/t-1

    # Mimic lost state on the discard path.
    monkeypatch.setattr(
        worktree_module.WorktreeManager,
        "load_for_task",
        staticmethod(lambda *_a, **_kw: None),
    )

    # MUST NOT raise even though branch task/t-1 still exists —
    # discard is the admin-close path.
    git_mgr.transition("T-1", TaskState.FAILED)
    assert git_mgr.get_task("T-1").state == TaskState.FAILED


# -- Child-driven epic auto-transitions (HATS-690 / Req 2 of HATS-688) --


def _epic_in(mgr, epic_id: str, state: TaskState) -> None:
    """Create an epic and walk it to ``state`` (execute or document)."""
    mgr.create_task(epic_id, "Epic")
    mgr.transition(epic_id, TaskState.PLAN)
    mgr.transition(epic_id, TaskState.EXECUTE)
    if state == TaskState.DOCUMENT:
        mgr.transition(epic_id, TaskState.DOCUMENT)


def test_children_of_filters_by_parent(mgr):
    mgr.create_task("EPIC", "Epic")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.create_task("OTHER", "Unrelated")
    mgr.create_task("GC", "Grandchild", parent_task="C1")

    ids = {c.id for c in mgr._children_of("EPIC")}
    assert ids == {"C1", "C2"}  # not OTHER, not the epic, not the grandchild


def test_epic_auto_advances_to_review_when_all_children_done(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    _walk_to_done(mgr, "C1")

    # Epic still execute while C2 is open.
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE

    # Driving the LAST child to done returns the epic delta.
    mgr.transition("C2", TaskState.PLAN)
    mgr.transition("C2", TaskState.EXECUTE)
    mgr.transition("C2", TaskState.DOCUMENT)
    mgr.transition("C2", TaskState.REVIEW)
    _card, auto = mgr.transition("C2", TaskState.DONE)

    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.EXECUTE, TaskState.REVIEW)
    ]
    assert any("Auto-advanced" in e.message for e in mgr.get_task("EPIC").work_log)


def test_epic_auto_advances_from_document_single_hop(mgr):
    _epic_in(mgr, "EPIC", TaskState.DOCUMENT)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW


def test_cancelled_child_does_not_block_advance(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.transition("C2", TaskState.CANCELLED, resolution="scope dropped")
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW


def test_failed_child_blocks_advance(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.transition("C2", TaskState.PLAN)
    mgr.transition("C2", TaskState.EXECUTE)
    mgr.transition("C2", TaskState.FAILED)
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE  # not advanced


def test_blocked_child_blocks_advance(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.transition("C2", TaskState.BLOCKED)
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE


def test_advance_requires_at_least_one_done(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.transition("C1", TaskState.CANCELLED, resolution="drop")
    _card, auto = mgr.transition("C2", TaskState.CANCELLED, resolution="drop")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE  # all cancelled, none done
    assert auto == []


def test_plan_epic_syncs_via_child_lifecycle(mgr, monkeypatch):
    """HATS-692 changed HATS-690's behaviour: a `plan` epic is no longer left
    untouched. Walking its child execute->done activates it (plan->execute) then
    advances it (->review) — without ever creating a worktree for the epic."""
    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))

    mgr.create_task("EPIC", "Epic")
    mgr.transition("EPIC", TaskState.PLAN)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")

    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    assert "EPIC" not in setup_calls  # invariant: epics never get a worktree


def test_brainstorm_epic_advances_on_completion(mgr):
    """HATS-789: walking a child to done under a brainstorm epic activates it on
    the execute hop, then advances it to review on done (the old D1 'leave
    brainstorm alone' guard is removed)."""
    mgr.create_task("EPIC", "Epic")  # brainstorm
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW


def test_zero_children_epic_not_advanced(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    # No children created; an unrelated task reaching done must not touch EPIC.
    mgr.create_task("SOLO", "Unrelated")
    _walk_to_done(mgr, "SOLO")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE


def test_create_under_done_epic_reopens_without_worktree(mgr, monkeypatch):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    mgr.transition("EPIC", TaskState.DONE)  # reviewer closes the epic
    assert mgr.get_task("EPIC").completed_at != ""

    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))

    _card, auto = mgr.create_task("C2", "New child", parent_task="EPIC")

    epic = mgr.get_task("EPIC")
    assert epic.state == TaskState.EXECUTE  # reopened
    assert epic.completed_at == ""  # completion cleared
    assert "EPIC" not in setup_calls  # Q3 caveat: no worktree for the epic
    assert [(t.ticket.id, t.to_state) for t in auto] == [("EPIC", TaskState.EXECUTE)]
    assert any("Auto-reopened" in e.message for e in epic.work_log)


def test_update_reparent_into_done_epic_reopens(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    mgr.transition("EPIC", TaskState.DONE)

    mgr.create_task("FREE", "Unparented live task")  # brainstorm
    _card, auto = mgr.update_task("FREE", parent_task="EPIC")

    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert [t.ticket.id for t in auto] == ["EPIC"]


def test_child_reopen_done_to_execute_reopens_epic(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    mgr.transition("EPIC", TaskState.DONE)

    _card, auto = mgr.transition("C1", TaskState.EXECUTE)  # child reopened

    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert [t.ticket.id for t in auto] == ["EPIC"]


def test_close_task_child_advances_epic(mgr):
    """D2: a child fast-closed to done completes its epic."""
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _card, auto = mgr.close_task("C1", "shipped on master")

    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    assert [t.ticket.id for t in auto] == ["EPIC"]


def test_return_contract_empty_for_parentless_task(mgr):
    mgr.create_task("SOLO", "No parent")
    card, auto = mgr.transition("SOLO", TaskState.PLAN)
    assert card.state == TaskState.PLAN
    assert auto == []


def test_epic_already_in_review_is_noop(mgr):
    _epic_in(mgr, "EPIC", TaskState.EXECUTE)
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    _walk_to_done(mgr, "C1")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    log_len = len(mgr.get_task("EPIC").work_log)

    # A second resolved child arriving while the epic is already in review
    # must not re-fire (review is not execute/document, and not done).
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    _card, auto = mgr.close_task("C2", "also shipped")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    assert auto == []
    assert len(mgr.get_task("EPIC").work_log) == log_len


# -- Epic activation + plan advance-fallback (HATS-692) --


def _epic_in_plan(mgr, epic_id: str) -> None:
    """Create an epic and leave it in plan (decomposed, not yet active)."""
    mgr.create_task(epic_id, "Epic")
    mgr.transition(epic_id, TaskState.PLAN)


def test_child_taken_activates_plan_epic(mgr):
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.transition("C1", TaskState.PLAN)
    _card, auto = mgr.transition("C1", TaskState.EXECUTE)  # child taken

    epic = mgr.get_task("EPIC")
    assert epic.state == TaskState.EXECUTE  # activated
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.PLAN, TaskState.EXECUTE)
    ]
    assert any("Auto-activated" in e.message for e in epic.work_log)


def test_activation_is_idempotent(mgr):
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.transition("C1", TaskState.PLAN)
    mgr.transition("C1", TaskState.EXECUTE)  # activates EPIC -> execute
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    log_len = len(mgr.get_task("EPIC").work_log)

    # Child moves further; epic already execute -> no second activation.
    _card, auto = mgr.transition("C1", TaskState.DOCUMENT)
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert auto == []
    assert len(mgr.get_task("EPIC").work_log) == log_len


def test_brainstorm_epic_activated(mgr):
    """HATS-789: a child taken into work activates even a brainstorm epic via a
    brainstorm -> plan -> execute multi-hop. An active child proves the epic is
    decomposed, so the old D1 'leave brainstorm alone' guard is removed."""
    mgr.create_task("EPIC", "Epic")  # stays in brainstorm
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.transition("C1", TaskState.PLAN)
    _card, auto = mgr.transition("C1", TaskState.EXECUTE)
    epic = mgr.get_task("EPIC")
    assert epic.state == TaskState.EXECUTE  # activated brainstorm -> execute
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.BRAINSTORM, TaskState.EXECUTE)
    ]
    assert any("Auto-activated" in e.message for e in epic.work_log)


def test_brainstorm_epic_fast_close_advances_to_review(mgr):
    """HATS-789 (symmetric): a brainstorm epic whose children are ALL fast-closed
    (brainstorm -> done, never active) advances brainstorm -> ... -> review."""
    mgr.create_task("EPIC", "Epic")  # brainstorm
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.close_task("C1", "shipped on master")
    _card, auto = mgr.close_task("C2", "shipped on master")
    epic = mgr.get_task("EPIC")
    assert epic.state == TaskState.REVIEW
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.BRAINSTORM, TaskState.REVIEW)
    ]
    assert any("Auto-advanced" in e.message for e in epic.work_log)


def test_reparent_active_child_into_plan_epic_activates(mgr):
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("FREE", "Live task")
    mgr.transition("FREE", TaskState.PLAN)
    mgr.transition("FREE", TaskState.EXECUTE)  # FREE is active, no parent yet
    _card, auto = mgr.update_task("FREE", parent_task="EPIC")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert [t.ticket.id for t in auto] == ["EPIC"]


def test_reparent_active_child_into_brainstorm_epic_activates(mgr):
    """HATS-789: reparenting an active child under a brainstorm epic activates it
    (mirrors the plan-epic reparent case)."""
    mgr.create_task("EPIC", "Epic")  # brainstorm
    mgr.create_task("FREE", "Live task")
    mgr.transition("FREE", TaskState.PLAN)
    mgr.transition("FREE", TaskState.EXECUTE)  # FREE active, no parent yet
    _card, auto = mgr.update_task("FREE", parent_task="EPIC")
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.BRAINSTORM, TaskState.EXECUTE)
    ]


def test_brainstorm_epic_activation_no_worktree(mgr, monkeypatch):
    """HATS-789 R3: activating a brainstorm epic via the auto-path gives it no
    worktree (epics are trackers — the multi-hop never calls _setup_worktree)."""
    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))
    mgr.create_task("EPIC", "Epic")  # brainstorm
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.transition("C1", TaskState.PLAN)
    mgr.transition("C1", TaskState.EXECUTE)  # activates EPIC brainstorm -> execute
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert "EPIC" not in setup_calls  # epic gets no worktree on the auto-path


def test_create_brainstorm_child_does_not_activate_plan_epic(mgr):
    _epic_in_plan(mgr, "EPIC")
    _card, auto = mgr.create_task("C1", "Child 1", parent_task="EPIC")
    assert mgr.get_task("EPIC").state == TaskState.PLAN  # brainstorm child, no work
    assert auto == []


def test_plan_epic_fast_close_advances_to_review(mgr):
    """The HATS-688 stranding bug: children fast-closed while epic in plan."""
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.close_task("C1", "shipped on master")
    _card, auto = mgr.close_task("C2", "shipped on master")

    epic = mgr.get_task("EPIC")
    assert epic.state == TaskState.REVIEW  # plan -> review fallback
    assert [(t.ticket.id, t.from_state, t.to_state) for t in auto] == [
        ("EPIC", TaskState.PLAN, TaskState.REVIEW)
    ]
    assert any("Auto-advanced" in e.message for e in epic.work_log)


def test_plan_epic_fast_close_mixed_done_cancelled_advances(mgr):
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.close_task("C1", "shipped")  # done
    _card, auto = mgr.transition("C2", TaskState.CANCELLED, resolution="drop")
    assert mgr.get_task("EPIC").state == TaskState.REVIEW  # >=1 done holds
    assert [t.ticket.id for t in auto] == ["EPIC"]


def test_plan_epic_all_cancelled_not_advanced(mgr):
    _epic_in_plan(mgr, "EPIC")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.create_task("C2", "Child 2", parent_task="EPIC")
    mgr.transition("C1", TaskState.CANCELLED, resolution="drop")
    _card, auto = mgr.transition("C2", TaskState.CANCELLED, resolution="drop")
    assert mgr.get_task("EPIC").state == TaskState.PLAN  # none done -> no advance
    assert auto == []


# -- Epic is a tracker, not an executable task (HATS-794) --


def test_manual_epic_execute_no_worktree(mgr, monkeypatch):
    """HATS-794: manually moving an epic (has children) to execute is a pure state
    flip — no worktree (epics are trackers; symmetric with the auto-path)."""
    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))
    mgr.create_task("EPIC", "Epic")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.transition("EPIC", TaskState.PLAN)
    mgr.transition("EPIC", TaskState.EXECUTE)
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert "EPIC" not in setup_calls  # epic gets no worktree on the manual path


def test_manual_non_epic_execute_still_worktree(mgr, monkeypatch):
    """HATS-794 (regression): a childless task still gets a worktree on execute."""
    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))
    mgr.create_task("SOLO", "Lone task")  # no children
    mgr.transition("SOLO", TaskState.PLAN)
    mgr.transition("SOLO", TaskState.EXECUTE)
    assert "SOLO" in setup_calls  # non-epic path unchanged


def test_manual_epic_execute_skips_plan_gate(tmp_path, monkeypatch):
    """HATS-794: an epic with an unfilled plan still enters execute (plan-gate
    waived); a childless task with the same empty scaffold is still gated."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (project / ".agent" / "STATE.md").write_text("")
    strict = TaskManager(project, prefix="T", strict_plan_check=True)
    monkeypatch.setattr(strict, "_setup_worktree", lambda task, **_kw: None)

    strict.create_task("T-1", "Epic")
    strict.create_task("T-2", "Child", parent_task="T-1")
    strict.transition("T-1", TaskState.PLAN)  # empty scaffold
    strict.transition("T-1", TaskState.EXECUTE)  # epic → plan-gate waived
    assert strict.get_task("T-1").state == TaskState.EXECUTE

    strict.create_task("T-3", "Solo")  # childless → still gated
    strict.transition("T-3", TaskState.PLAN)
    with pytest.raises(EmptyPlanError):
        strict.transition("T-3", TaskState.EXECUTE)


def test_manual_epic_reopen_no_worktree(mgr, monkeypatch):
    """HATS-794: reopening a done epic (DONE → EXECUTE) takes no worktree either."""
    setup_calls: list[str] = []
    monkeypatch.setattr(mgr, "_setup_worktree", lambda task, **_kw: setup_calls.append(task.id))
    mgr.create_task("EPIC", "Epic")
    mgr.create_task("C1", "Child 1", parent_task="EPIC")
    mgr.close_task("C1", "shipped")  # C1 done → EPIC auto-advances to review
    assert mgr.get_task("EPIC").state == TaskState.REVIEW
    mgr.transition("EPIC", TaskState.DONE)  # reviewer closes the epic
    setup_calls.clear()

    mgr.transition("EPIC", TaskState.EXECUTE)  # reopen done → execute
    assert mgr.get_task("EPIC").state == TaskState.EXECUTE
    assert "EPIC" not in setup_calls  # reopened epic: still a tracker, no worktree
