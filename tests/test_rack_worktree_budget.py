"""Worktree handler timeout budget (HATS-1043 step 7, R9 / HATS-1015).

The worktree extension carries a generous per-git budget (default 60) threaded
into every wt shell-out; a git op that blows the budget raises through the
existing in-lock exception path → the transition aborts before persist and is
journaled. The hook-runner default (30) and the worktree budget default (60)
are unchanged.
"""

from __future__ import annotations

import subprocess

import pytest

from ai_hats.rack_consumers import HOOK_TIMEOUT, HookRunnerExtension
from ai_hats.rack_wiring import WORKTREE_BUDGET, WorktreeExtension
from ai_hats_rack import Kernel
from ai_hats_rack.fsm import load_topology
from ai_hats_wt import WorktreeManager


@pytest.fixture
def tasks_dir(tmp_path):
    return tmp_path / "tasks"


@pytest.fixture
def cwd(tmp_path):
    return tmp_path


class _TimeoutEffects:
    """A worktree effects double whose teardown blows the git budget."""

    def setup(self, *a, **k):
        return None

    def teardown(self, *a, **k):
        raise subprocess.TimeoutExpired(cmd="git merge", timeout=0.01)

    def discard_if_empty(self, *a, **k):
        return False

    def assert_canonical_base(self):
        return None


class _Sink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def _kernel(tasks_dir, effects, sink):
    topology = load_topology()
    wt = WorktreeExtension(tasks_dir, effects=effects, topology=topology)
    return Kernel(tasks_dir, prefix="T", topology=topology, subscribers=[wt], journal_sink=sink)


def test_worktree_git_timeout_aborts_the_transition_and_journals(tasks_dir, cwd):
    sink = _Sink()
    k = _kernel(tasks_dir, _TimeoutEffects(), sink)
    k.create(actor="t", caller_cwd=cwd, task_id="T-1", title="a")
    for st in ("plan", "execute", "document", "review"):
        k.transition("T-1", st, actor="t", caller_cwd=cwd)

    # teardown blows the budget → in-lock error → the transition aborts.
    with pytest.raises(subprocess.TimeoutExpired):
        k.transition("T-1", "done", actor="t", caller_cwd=cwd)

    assert k.get("T-1").state == "review"  # nothing persisted before the abort
    rec = sink.records[-1]
    assert rec.event_key == "edge:review--done" and rec.result == "aborted"
    assert any(o.subscriber == "worktree" and o.outcome == "error" for o in rec.outcomes)


def test_default_budget_threads_into_the_worktree_effects(tasks_dir):
    topology = load_topology()
    assert WORKTREE_BUDGET == 60.0
    wt = WorktreeExtension(tasks_dir, topology=topology)  # default effects
    assert wt._budget == 60.0
    assert wt._effects._git_timeout == 60.0
    tight = WorktreeExtension(tasks_dir, topology=topology, budget=5.0)
    assert tight._effects._git_timeout == 5.0


def test_manager_threads_git_timeout_to_the_subprocess(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("ai_hats_wt.manager.subprocess.run", fake_run)
    mgr = WorktreeManager(tmp_path, branch_name="task/x", git_timeout=42.0)
    mgr._git("status")
    assert captured["timeout"] == 42.0  # the budget is the per-call default
    mgr._git("status", timeout=7.0)
    assert captured["timeout"] == 7.0  # an explicit per-call timeout still wins


def test_hook_runner_default_timeout_is_unchanged(tmp_path):
    assert HOOK_TIMEOUT == 30.0
    ext = HookRunnerExtension(
        tmp_path / "hooks", tmp_path / "tasks", project_dir=tmp_path, topology=load_topology()
    )
    assert ext.timeout == 30.0
