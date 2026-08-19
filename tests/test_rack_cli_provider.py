"""Integrator wired-kernel provider for the ``rack`` CLI (HATS-1038 C1):
typed wt-exception rendering (parity with the old task handlers, HATS-1019)
and the post-create STATE.md refresh."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_wt import WorktreeMergeConsentError, WorktreeStateLostError

from ai_hats.rack_cli_provider import CliKernelProvider, cli_factory
from ai_hats_rack.resolver import RackRoot
from ai_hats_rack.selectors import Edge


def test_factory_returns_provider():
    assert isinstance(cli_factory(), CliKernelProvider)


# ----- typed wt-error rendering ----------------------------------------------


def test_merge_consent_renders_review_handoff(capsys):
    exc = WorktreeMergeConsentError("task/hats-1", "master")
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "review consent required" in err
    # `export`, on its own line: the inline prefix is refused as a self-grant, so a
    # recipe spelling it that way would send the agent at a wall (HATS-1639).
    assert "export AI_HATS_MERGE_ACK=1" in err
    assert "ai-hats wt merge task/hats-1" in err
    assert "AI_HATS_MERGE_ACK=1 ai-hats wt merge" not in err
    assert "rack transition HATS-1 --state done" in err


def test_state_lost_renders_recovery_recipe(capsys):
    exc = WorktreeStateLostError("HATS-1", "task/hats-1")
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "worktree state lost" in err
    assert "git merge --no-ff task/hats-1" in err


def test_drift_renders_rebase_first_recipe(capsys, tmp_path, monkeypatch):
    """HATS-1307: the recipe leads with the remedy `rack transition` can use."""
    from ai_hats_wt import WorktreeDriftError

    (tmp_path / ".agent").mkdir()
    monkeypatch.chdir(tmp_path)
    wt_path = tmp_path / "wt-hats-1"
    exc = WorktreeDriftError(
        "base 'master' moved:\nlocal: 1 commit\naffected paths:\n  other.txt",
        branch_name="task/hats-1",
        base_branch="master",
        worktree_path=wt_path,
    )
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "drifted" in err
    assert "other.txt" in err
    assert f"cd {wt_path}" in err
    assert "git rebase master" in err
    assert f"cd {Path.cwd()}" in err
    assert "rack transition HATS-1 --state done" in err
    # --accept-drift demoted to conscious acceptance, still on its own surface.
    assert "ai-hats wt merge --accept-drift task/hats-1" in err
    assert "belongs to `wt merge`, not `rack transition`" in err


def test_drift_recipe_without_refs_uses_placeholders(capsys, tmp_path, monkeypatch):
    """A drift error raised without ref attributes still renders a shaped recipe."""
    from ai_hats_wt import WorktreeDriftError

    (tmp_path / ".agent").mkdir()
    monkeypatch.chdir(tmp_path)
    handled = CliKernelProvider().handle_error(
        WorktreeDriftError("moved"), as_json=False, task_id="HATS-1"
    )
    assert handled is True
    err = capsys.readouterr().err
    assert "cd <worktree>" in err
    assert "git rebase <base>" in err


def test_base_branch_mismatch_renders_checkout_recipe(capsys, tmp_path, monkeypatch):
    from ai_hats_wt import WorktreeBaseBranchMismatchError

    (tmp_path / ".agent").mkdir()
    monkeypatch.chdir(tmp_path)
    exc = WorktreeBaseBranchMismatchError("wandered-feature", "master")
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "base branch mismatch" in err
    assert "wandered-feature" in err
    assert f"cd {Path.cwd()}" in err
    assert "git checkout master" in err
    assert "rack transition HATS-1 --state done" in err


def test_recipe_omits_cd_when_no_project_root(capsys, tmp_path, monkeypatch):
    from ai_hats_wt import WorktreeBaseBranchMismatchError

    monkeypatch.chdir(tmp_path)  # no `.agent` / ai-hats.yaml anywhere above
    handled = CliKernelProvider().handle_error(
        WorktreeBaseBranchMismatchError("wandered-feature", "master"),
        as_json=False,
        task_id="HATS-1",
    )
    assert handled is True
    err = capsys.readouterr().err
    assert "cd " not in err
    assert "git checkout master" in err


def test_merge_consent_json_carries_code(capsys):
    exc = WorktreeMergeConsentError("task/hats-1", "master")
    handled = CliKernelProvider().handle_error(exc, as_json=True, task_id="HATS-1")
    assert handled is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "worktree_merge_consent"


def test_new_recipe_errors_carry_distinct_json_codes(capsys):
    from ai_hats_wt import WorktreeBaseBranchMismatchError, WorktreeDriftError

    provider = CliKernelProvider()
    provider.handle_error(WorktreeDriftError("moved"), as_json=True, task_id="HATS-1")
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "worktree_drift"

    provider.handle_error(
        WorktreeBaseBranchMismatchError("wandered", "master"), as_json=True, task_id="HATS-1"
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "worktree_base_branch_mismatch"
    assert "wandered" in payload["error"]["message"]


def test_unknown_wt_error_is_typed_not_traceback(capsys):
    from ai_hats_wt import WorktreeBaseBranchError

    handled = CliKernelProvider().handle_error(
        WorktreeBaseBranchError("task/hats-9", ["master"]), as_json=False, task_id="HATS-1"
    )
    assert handled is True
    assert "Refused (worktree)" in capsys.readouterr().err


def test_non_wt_exception_is_not_owned(capsys):
    handled = CliKernelProvider().handle_error(ValueError("boom"), as_json=False, task_id="HATS-1")
    assert handled is False


# ----- wired kernel ----------------------------------------------------------


def test_build_kernel_wires_the_consumer_check_runner(tmp_path):
    """HATS-1141: the pack this provider passes is no longer empty, and the
    runner reaches the kernel subscribed to the edges of ITS topology."""
    from ai_hats_rack.dispatch import Phase

    tasks_dir = tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)
    root = RackRoot(
        project_dir=tmp_path, tasks_dir=tasks_dir, backlog_owner=tmp_path, prefix="HATS"
    )

    kernel = CliKernelProvider().build_kernel(root, tmp_path)

    on_done = [
        s.name
        for s in kernel._dispatcher.subscribers_for_edge(Edge("review", "done"), Phase.IN_LOCK)
    ]
    assert "checks" in on_done
    assert on_done.index("checks") < on_done.index("worktree")


def test_the_check_port_composes_the_backlogs_owner_not_the_anchor(tmp_path):
    """HATS-1573: composition follows the backlog, not the caller's checkout.

    Under an explicit ``--tasks-dir`` the anchor is a different project
    entirely, and composing ITS role is how a checkout's gates reached a backlog
    it never declared (the HATS-1538 class, from the other side).
    """
    sandbox = tmp_path / "sbx"
    tasks_dir = sandbox / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    root = RackRoot(
        project_dir=tmp_path / "foreign",
        tasks_dir=tasks_dir,
        backlog_owner=sandbox,
        prefix="HATS",
    )

    port = CliKernelProvider().check_port(root, tasks_dir)

    assert port.backlog_owner == sandbox


# ----- post-create STATE.md refresh ------------------------------------------


def test_after_create_never_writes_into_a_foreign_checkout(tmp_path):
    """HATS-1573: STATE.md follows the backlog, not wherever the operator stands.

    With an explicit --tasks-dir the anchor is another project entirely, and
    indexing into ITS tracker is a write the operation never asked for.
    """
    from ai_hats_rack.kernel import Kernel

    foreign = tmp_path / "foreign"
    (foreign / ".agent" / "ai-hats").mkdir(parents=True)
    sandbox = tmp_path / "sbx"
    tasks_dir = sandbox / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)
    kernel = Kernel(tasks_dir, prefix="HATS")
    result = kernel.create(actor="test", caller_cwd=sandbox, title="demo")
    root = RackRoot(project_dir=foreign, tasks_dir=tasks_dir, backlog_owner=sandbox, prefix="HATS")

    CliKernelProvider().after_create(root, result)

    assert (sandbox / ".agent" / "ai-hats" / "STATE.md").is_file()
    assert not (foreign / ".agent" / "ai-hats" / "STATE.md").exists()


def test_a_transition_indexes_the_backlog_it_moved_a_card_in(tmp_path):
    """The twin of the test above on the FSM road — the half that was missed.

    ``DerivedViewsExtension`` replaces STATE.md wholesale, so a kernel pointed at
    the anchor did not merely leave a stray file: it rewrote the enclosing
    project's index from a backlog that project never declared (HATS-1573).
    """
    from ai_hats.tracker_wiring import tracker_paths

    foreign = tmp_path / "foreign"
    (foreign / ".agent" / "ai-hats").mkdir(parents=True)
    foreign_state = tracker_paths(foreign).state_md_path
    foreign_state.write_text("# the anchor's own index\n")
    sandbox = tmp_path / "sbx"
    tasks_dir = sandbox / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)
    root = RackRoot(project_dir=foreign, tasks_dir=tasks_dir, backlog_owner=sandbox, prefix="HATS")
    kernel = CliKernelProvider().build_kernel(root, sandbox)
    kernel.create(actor="test", caller_cwd=sandbox, task_id="HATS-1", title="demo")

    kernel.transition("HATS-1", "plan", actor="test", caller_cwd=sandbox)

    assert foreign_state.read_text() == "# the anchor's own index\n"
    assert "HATS-1" in tracker_paths(sandbox).state_md_path.read_text()


def test_after_create_indexes_new_card(tmp_path):
    from ai_hats.tracker_wiring import tracker_paths
    from ai_hats_rack.kernel import Kernel

    tasks_dir = tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)
    kernel = Kernel(tasks_dir, prefix="HATS")
    result = kernel.create(actor="test", caller_cwd=tmp_path, title="demo")

    root = RackRoot(
        project_dir=tmp_path, tasks_dir=tasks_dir, backlog_owner=tmp_path, prefix="HATS"
    )
    state_md = tracker_paths(tmp_path).state_md_path
    assert not state_md.exists()  # create takes no FSM edge → views never fired

    CliKernelProvider().after_create(root, result)

    assert state_md.exists()
    assert result.task.id in state_md.read_text(encoding="utf-8")
