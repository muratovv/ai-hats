"""CLI tests for `ai-hats task transition` — focused on HATS-168 cancelled flow.

Manager-level coverage for cancelled lives in test_state.py; this file
exercises the click validation layer that enforces --resolution at the edge.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.models import TaskState
from ai_hats.state import TaskManager


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Bare project layout that `_task_manager` / `_project_dir` accept."""
    (tmp_path / ".agent" / "backlog" / "tasks").mkdir(parents=True)
    (tmp_path / ".agent" / "STATE.md").write_text("")
    return tmp_path


@pytest.fixture
def cli(monkeypatch, project_dir):
    monkeypatch.chdir(project_dir)
    return CliRunner()


def _seed_task(project_dir: Path, task_id: str = "T-1") -> None:
    TaskManager(project_dir, prefix="T").create_task(task_id, "Sample task")


def test_transition_cancelled_requires_resolution(cli, project_dir):
    """Bare `transition T-1 cancelled` (no --resolution) must fail loudly."""
    _seed_task(project_dir)

    result = cli.invoke(main, ["task", "transition", "T-1", "cancelled"])

    assert result.exit_code == 1, result.output
    assert "resolution" in result.output.lower()

    # Card untouched — still in brainstorm.
    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.BRAINSTORM


def test_transition_cancelled_with_resolution_succeeds(cli, project_dir):
    _seed_task(project_dir)

    result = cli.invoke(
        main,
        ["task", "transition", "T-1", "cancelled", "--resolution", "duplicate of T-99"],
    )
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.CANCELLED
    assert t.resolution == "duplicate of T-99"
    assert t.completed_at != ""


def test_transition_cancelled_blank_resolution_rejected(cli, project_dir):
    """Empty/whitespace resolution counts as missing — protects against
    `--resolution ""` slipping through as 'set but empty'."""
    _seed_task(project_dir)

    result = cli.invoke(
        main, ["task", "transition", "T-1", "cancelled", "--resolution", "   "]
    )
    assert result.exit_code == 1, result.output
    assert "resolution" in result.output.lower()


def test_transition_to_other_states_does_not_require_resolution(cli, project_dir):
    """Sanity: --resolution validation must not leak into normal transitions."""
    _seed_task(project_dir)

    result = cli.invoke(main, ["task", "transition", "T-1", "plan"])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.PLAN


def test_task_list_hides_cancelled_by_default(cli, project_dir):
    """`task list` (default) hides cancelled the same way it hides done/failed."""
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "Active task")
    mgr.create_task("T-2", "Closed task")
    mgr.transition("T-2", TaskState.CANCELLED, resolution="dup")

    result = cli.invoke(main, ["task", "list"])
    assert result.exit_code == 0, result.output
    assert "T-1" in result.output
    assert "T-2" not in result.output

    result_all = cli.invoke(main, ["task", "list", "--all"])
    assert "T-1" in result_all.output
    assert "T-2" in result_all.output


# -- HATS-198: parent / depends_on flags --


def test_create_with_parent_and_depends_flags(cli, project_dir):
    """`task create --parent-task X --depends-on Y --depends-on Z` wires both fields."""
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-0", "Epic")
    mgr.create_task("T-9", "Blocker A")
    mgr.create_task("T-8", "Blocker B")

    result = cli.invoke(main, [
        "task", "create", "Child",
        "--id", "T-1",
        "--parent-task", "T-0",
        "--depends-on", "T-9",
        "--depends-on", "T-8",
    ])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.parent_task == "T-0"
    assert t.depends_on == ["T-9", "T-8"]


def test_create_warns_on_missing_refs(cli, project_dir):
    """Unknown refs MUST warn on stdout but MUST NOT abort the create."""
    result = cli.invoke(main, [
        "task", "create", "Forward ref",
        "--id", "T-1",
        "--parent-task", "T-NOPE",
        "--depends-on", "T-99",
    ])
    assert result.exit_code == 0, result.output
    assert "warning" in result.output.lower()
    assert "T-NOPE" in result.output
    assert "T-99" in result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t is not None
    assert t.parent_task == "T-NOPE"
    assert t.depends_on == ["T-99"]


def test_create_duplicate_id_rejected(cli, project_dir):
    """`task create --id X` must refuse to overwrite an existing task."""
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "Original", description="keep me")

    result = cli.invoke(main, [
        "task", "create", "Overwriter",
        "--id", "T-1",
        "--description", "should not land",
    ])
    assert result.exit_code == 1, result.output
    assert "already exists" in result.output.lower()

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.title == "Original"
    assert t.description == "keep me"


def test_create_self_reference_rejected(cli, project_dir):
    result = cli.invoke(main, [
        "task", "create", "Self parent",
        "--id", "T-1",
        "--parent-task", "T-1",
    ])
    assert result.exit_code == 1, result.output
    assert "own parent" in result.output.lower()
    assert TaskManager(project_dir, prefix="T").get_task("T-1") is None


def test_update_set_and_clear_parent(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-0", "Epic")
    mgr.create_task("T-1", "Child")

    r1 = cli.invoke(main, ["task", "update", "T-1", "--parent-task", "T-0"])
    assert r1.exit_code == 0, r1.output
    assert TaskManager(project_dir, prefix="T").get_task("T-1").parent_task == "T-0"

    r2 = cli.invoke(main, ["task", "update", "T-1", "--clear-parent"])
    assert r2.exit_code == 0, r2.output
    assert TaskManager(project_dir, prefix="T").get_task("T-1").parent_task == ""


def test_update_parent_and_clear_parent_mutually_exclusive(cli, project_dir):
    TaskManager(project_dir, prefix="T").create_task("T-1", "Sample")
    result = cli.invoke(main, [
        "task", "update", "T-1",
        "--parent-task", "T-0",
        "--clear-parent",
    ])
    assert result.exit_code == 1, result.output
    assert "mutually exclusive" in result.output.lower()


def test_update_add_remove_depends(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-9", "Dep A")
    mgr.create_task("T-8", "Dep B")
    mgr.create_task("T-7", "Dep C")
    mgr.create_task("T-1", "Blocked", depends_on=["T-9", "T-8"])

    result = cli.invoke(main, [
        "task", "update", "T-1",
        "--add-depends", "T-7",
        "--remove-depends", "T-9",
    ])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert "T-7" in t.depends_on
    assert "T-9" not in t.depends_on
    assert "T-8" in t.depends_on


def test_show_displays_blocked_by_section(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-9", "Blocker title")
    mgr.create_task("T-1", "Blocked", depends_on=["T-9"])

    result = cli.invoke(main, ["task", "show", "T-1"])
    assert result.exit_code == 0, result.output
    assert "Blocked by" in result.output
    assert "T-9" in result.output
    assert "Blocker title" in result.output
    # The blocker's state must render — guards against rich-markup eating
    # bracketed identifiers like `[brainstorm]`.
    assert "brainstorm" in result.output


def test_list_search_matches_depends(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-9", "Some blocker")
    mgr.create_task("T-1", "First", depends_on=["T-9"])
    mgr.create_task("T-2", "Unrelated")

    result = cli.invoke(main, ["task", "list", "--search", "T-9"])
    assert result.exit_code == 0, result.output
    # T-9 itself matches by id; T-1 must match via depends_on; T-2 must NOT.
    assert "T-1" in result.output
    assert "T-2" not in result.output


# -- HATS-371: close / link / force / show backlinks ----------------------


def test_close_requires_resolution_option(cli, project_dir):
    """click --required flag rejects missing --resolution."""
    _seed_task(project_dir)
    result = cli.invoke(main, ["task", "close", "T-1"])
    assert result.exit_code != 0
    # click renders "Missing option '--resolution'" — case may vary by version.
    assert "resolution" in result.output.lower()


def test_close_from_brainstorm(cli, project_dir):
    _seed_task(project_dir)
    result = cli.invoke(
        main, ["task", "close", "T-1", "--resolution", "shipped in 6e7ddd5"]
    )
    assert result.exit_code == 0, result.output
    assert "Closed" in result.output
    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.DONE
    assert t.resolution == "shipped in 6e7ddd5"


def test_force_transition_with_reason(cli, project_dir):
    _seed_task(project_dir)
    mgr = TaskManager(project_dir, prefix="T", strict_plan_check=False)
    mgr.transition("T-1", TaskState.PLAN)

    result = cli.invoke(
        main,
        [
            "task", "transition", "T-1", "brainstorm",
            "--force", "--reason", "plan started by mistake",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Forced" in result.output
    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.state == TaskState.BRAINSTORM


def test_force_without_reason_rejected(cli, project_dir):
    _seed_task(project_dir)
    mgr = TaskManager(project_dir, prefix="T", strict_plan_check=False)
    mgr.transition("T-1", TaskState.PLAN)

    result = cli.invoke(
        main, ["task", "transition", "T-1", "brainstorm", "--force"]
    )
    assert result.exit_code == 1
    assert "reason" in result.output.lower()


def test_link_related_then_show_renders_both_sides(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "First")
    mgr.create_task("T-2", "Second")

    r = cli.invoke(main, ["task", "link", "T-1", "T-2", "--type", "related"])
    assert r.exit_code == 0, r.output
    assert "Linked" in r.output

    show1 = cli.invoke(main, ["task", "show", "T-1"])
    assert "Related" in show1.output
    assert "T-2" in show1.output
    show2 = cli.invoke(main, ["task", "show", "T-2"])
    assert "Related" in show2.output
    assert "T-1" in show2.output


def test_link_fold_then_show_renders_subsumed_on_target(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "Folded")
    mgr.create_task("T-2", "Keeper")

    r = cli.invoke(main, ["task", "link", "T-1", "T-2", "--type", "fold"])
    assert r.exit_code == 0, r.output
    assert "Folded" in r.output

    show1 = cli.invoke(main, ["task", "show", "T-1"])
    assert "Folded into" in show1.output

    show2 = cli.invoke(main, ["task", "show", "T-2"])
    assert "Subsumed" in show2.output
    assert "T-1" in show2.output


def test_unlink_removes_relation(cli, project_dir):
    mgr = TaskManager(project_dir, prefix="T")
    mgr.create_task("T-1", "A")
    mgr.create_task("T-2", "B")
    mgr.add_link("T-1", "T-2", link_type="related")

    r = cli.invoke(main, ["task", "unlink", "T-1", "T-2", "--type", "related"])
    assert r.exit_code == 0, r.output
    assert mgr.get_task("T-1").related == []
    assert mgr.get_task("T-2").related == []


# ---------------------------------------------------------------------------
# HATS-541 — `transition done` recovery-hint when worktree state is lost
# ---------------------------------------------------------------------------


def test_transition_done_lost_state_prints_recovery_hint(
    cli, project_dir, monkeypatch
):
    """HATS-541: the CLI handler must surface a recovery recipe instead of
    leaking a raw stack trace when ``WorktreeStateLostError`` fires.

    Exercises the cli/task.py exception handler in isolation by raising
    ``WorktreeStateLostError`` directly from a monkeypatched
    ``TaskManager.transition`` — avoids dragging the full worktree
    lifecycle into a CLI-layer test.
    """
    from ai_hats import state as state_module
    from ai_hats.worktree import WorktreeStateLostError

    _seed_task(project_dir)
    real_transition = state_module.TaskManager.transition

    def fake_transition(self, task_id, new_state, **kwargs):
        if new_state == TaskState.DONE:
            raise WorktreeStateLostError(task_id, f"task/{task_id.lower()}")
        return real_transition(self, task_id, new_state, **kwargs)

    monkeypatch.setattr(state_module.TaskManager, "transition", fake_transition)

    result = cli.invoke(main, ["task", "transition", "T-1", "done"])

    assert result.exit_code == 1, result.output
    # Refusal banner.
    assert "Refused" in result.output
    assert "worktree state lost" in result.output.lower()
    # Branch name + recovery commands present.
    assert "task/t-1" in result.output
    assert "git merge --abort" in result.output
    assert "git merge --no-ff task/t-1" in result.output
    assert "ai-hats task transition T-1 done" in result.output
    # Friendly cause hint.
    assert "earlier" in result.output.lower() and "merge failed" in result.output.lower()


# -- HATS-492: --description-file (heredoc-fragility fix) --

# Content that mangles `-d "$(cat <<EOF ...)"`: backticks, $(...), an
# unbalanced paren, a nested ``` fence, and a bare `EOF` terminator line.
_GNARLY_DESC = (
    "## Repro\n"
    "```python\n"
    "x = `backtick` + $(whoami)\n"
    "y = (unbalanced\n"
    "```\n"
    "EOF\n"
    "field: value\n"
)


def test_create_description_file_preserves_content(cli, project_dir):
    """`task create --description-file F` stores F verbatim (no shell mangling)."""
    desc = project_dir / "desc.md"
    desc.write_text(_GNARLY_DESC)

    result = cli.invoke(
        main, ["task", "create", "Gnarly", "--id", "T-1", "--description-file", str(desc)]
    )
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.description == _GNARLY_DESC


def test_create_description_file_mutually_exclusive_with_d(cli, project_dir):
    """`-d` and `--description-file` together → UsageError (exit 2), no card written."""
    desc = project_dir / "desc.md"
    desc.write_text("from file")

    result = cli.invoke(main, [
        "task", "create", "Clash", "--id", "T-1",
        "-d", "inline", "--description-file", str(desc),
    ])
    assert result.exit_code == 2, result.output
    assert "mutually exclusive" in result.output.lower()
    # Resolution happens before any write — no card leaks.
    assert not (project_dir / ".agent" / "backlog" / "tasks" / "T-1").exists()


def test_create_description_file_missing_is_friendly(cli, project_dir):
    """Unreadable path → friendly UsageError naming the flag + path, no traceback."""
    result = cli.invoke(main, [
        "task", "create", "Ghost", "--id", "T-1",
        "--description-file", str(project_dir / "nope.md"),
    ])
    assert result.exit_code == 2, result.output
    assert "--description-file" in result.output
    assert "nope.md" in result.output
    assert "Traceback" not in result.output


def test_create_description_file_empty_yields_empty(cli, project_dir):
    """An empty file is a valid (empty) description — same as `-d ''`."""
    empty = project_dir / "empty.md"
    empty.write_text("")

    result = cli.invoke(main, [
        "task", "create", "Blank", "--id", "T-1", "--description-file", str(empty),
    ])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.description == ""


def test_update_description_file_replaces_verbatim(cli, project_dir):
    """`task update --description-file F` rewrites the description from F verbatim."""
    TaskManager(project_dir, prefix="T").create_task("T-1", "Title", description="old")
    desc = project_dir / "new.md"
    desc.write_text(_GNARLY_DESC)

    result = cli.invoke(main, ["task", "update", "T-1", "--description-file", str(desc)])
    assert result.exit_code == 0, result.output

    t = TaskManager(project_dir, prefix="T").get_task("T-1")
    assert t.description == _GNARLY_DESC
