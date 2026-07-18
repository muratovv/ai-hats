"""Integrator wired-kernel provider for the ``rack`` CLI (HATS-1038 C1):
typed wt-exception rendering (parity with the old task handlers, HATS-1019)
and the post-create STATE.md refresh."""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats_wt import WorktreeMergeConsentError, WorktreeStateLostError

from ai_hats.rack_cli_provider import CliKernelProvider, cli_factory
from ai_hats_rack.resolver import RackRoot


def test_factory_returns_provider():
    assert isinstance(cli_factory(), CliKernelProvider)


# ----- typed wt-error rendering ----------------------------------------------


def test_merge_consent_renders_review_handoff(capsys):
    exc = WorktreeMergeConsentError("task/hats-1", "master")
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "review consent required" in err
    assert "AI_HATS_MERGE_ACK=1 ai-hats wt merge task/hats-1" in err
    assert "rack transition HATS-1 --state done" in err


def test_state_lost_renders_recovery_recipe(capsys):
    exc = WorktreeStateLostError("HATS-1", "task/hats-1")
    handled = CliKernelProvider().handle_error(exc, as_json=False, task_id="HATS-1")
    assert handled is True
    err = capsys.readouterr().err
    assert "worktree state lost" in err
    assert "git merge --no-ff task/hats-1" in err


def test_merge_consent_json_carries_code(capsys):
    exc = WorktreeMergeConsentError("task/hats-1", "master")
    handled = CliKernelProvider().handle_error(exc, as_json=True, task_id="HATS-1")
    assert handled is True
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "worktree_merge_consent"


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


# ----- post-create STATE.md refresh ------------------------------------------


def test_after_create_indexes_new_card(tmp_path):
    from ai_hats.tracker_wiring import tracker_paths
    from ai_hats_rack.kernel import Kernel

    tasks_dir = tmp_path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)
    kernel = Kernel(tasks_dir, prefix="HATS")
    result = kernel.create(actor="test", caller_cwd=tmp_path, title="demo")

    root = RackRoot(project_dir=tmp_path, tasks_dir=tasks_dir, prefix="HATS")
    state_md = tracker_paths(tmp_path).state_md_path
    assert not state_md.exists()  # create takes no FSM edge → views never fired

    CliKernelProvider().after_create(root, result)

    assert state_md.exists()
    assert result.task.id in state_md.read_text(encoding="utf-8")
