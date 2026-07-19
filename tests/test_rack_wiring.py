"""Rack integrator assembly pins (HATS-1022): fire-position ratification
(fix #1 — a gate abort leaves NO ownership/worktree side effects, confirmed
against the REAL extensions), subscriber ordering, journal auditability of
refusals, and the wired derived views."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats_rack import OperationAborted
from ai_hats_rack.dispatch import Phase
from ai_hats_rack.extensions import standalone_extensions
from ai_hats.paths import worktrees_dir
from ai_hats.rack_wiring import build_rack_kernel
from ai_hats_wt import WorktreeManager

pytestmark = pytest.mark.integration

_FILLED_PLAN = (
    "# Plan\n\n## Requirements\nrack.\n\n## Scope & Out-of-scope\nin/out\n\n"
    "## Steps\n- [ ] do\n\n## Verification Protocol\npytest\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 — fixed argv, test helper
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "project"
    p.mkdir()
    _git(p, "init", "-b", "master")
    _git(p, "config", "user.email", "t@t.t")
    _git(p, "config", "user.name", "t")
    (p / "README.md").write_text("# t")
    _git(p, "add", ".")
    _git(p, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    (p / ".agent").mkdir()
    return p


def _kernel(project: Path, **kwargs):
    return build_rack_kernel(
        project,
        tasks_dir=project / ".agent" / "tasks",
        state_md_path=project / ".agent" / "STATE.md",
        prefix="T",
        **kwargs,
    )


class _Sink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def test_gate_abort_leaves_no_ownership_and_no_worktree(project, monkeypatch):
    """Ratification of fix #1 with the real extensions: the plan-gate fires
    before ownership claim and worktree setup, so its abort leaves zero
    side effects — no registry record, no worktree, zero bytes on the card."""
    monkeypatch.setenv("AI_HATS_SESSION_ID", "sess-a")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    sink = _Sink()
    kernel = _kernel(project, journal_sink=sink)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    before = (kernel.tasks_dir / "T-1" / "task.yaml").read_bytes()

    with pytest.raises(OperationAborted) as exc_info:  # empty scaffold → gate refuses
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "plan-gate"
    registry = kernel.tasks_dir.parent / "ownership.json"
    assert not registry.exists(), "gate abort must not leave an ownership claim"
    assert WorktreeManager.load_for_task(
        project, "T-1", state_dir=worktrees_dir(project)
    ) is None, "gate abort must not leave a worktree"
    assert not WorktreeManager.branch_exists(project, "task/t-1")
    assert (kernel.tasks_dir / "T-1" / "task.yaml").read_bytes() == before

    # PROP-004: the refusal itself is auditable — journaled, not swallowed.
    refusal = sink.records[-1]
    assert refusal.event_key == "edge:plan--execute"
    outcomes = {o.subscriber: o.outcome for o in refusal.outcomes}
    assert outcomes["plan-gate"] == "abort"
    assert "ownership" not in outcomes, "claim must not have run after the gate abort"


def test_in_lock_order_reproduces_the_tracker_sequence(project):
    """Priority wiring pin: single-slot → frozen-integrity → gate → claim →
    worktree on entering execute; teardown → release on leaving (HATS-955
    claim-before-effects, HATS-1031 integrity-before-gate)."""
    kernel = _kernel(project)
    into_execute = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    ]
    assert into_execute == [
        "ownership-single-slot",
        "frozen-integrity",
        "plan-gate",
        "ownership",
        "worktree",
    ]

    # stamp-lifecycle (declared, priority 12) now rides in-lock into `done`.
    to_done = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:review--done", Phase.IN_LOCK)
    ]
    assert to_done == [
        "ownership-single-slot",
        "frozen-integrity",
        "stamp-lifecycle",
        "worktree",
        "ownership-release",
    ]

    epicify = [s.name for s in kernel._dispatcher.subscribers_for("epicify", Phase.POST_LOCK)]
    assert epicify == ["ownership-release", "worktree", "epic-automation", "derived-views"]


def test_reopen_edge_skips_gate_but_clear_lifecycle_fires(project):
    """The reopen edge (done→execute) opts OUT of plan-gate via the declarative
    skip, while clear-lifecycle binds to that exact edge (ADR-0017 §3)."""
    kernel = _kernel(project)
    reopen = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:done--execute", Phase.IN_LOCK)
    ]
    assert "plan-gate" not in reopen  # reopen is not gated (HATS-328, declarative skip)
    assert "clear-lifecycle" in reopen  # completed_at cleared on the declared edge


def test_migrated_handler_subscribes_once_per_edge(project):
    """Double-subscription guard (HATS-1043): a migrated handler comes ONLY via
    the declaration channel, never also self-subscribing — exactly once/edge."""
    kernel = _kernel(project)
    into_execute = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    ]
    assert into_execute.count("plan-gate") == 1
    into_plan = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:brainstorm--plan", Phase.IN_LOCK)
    ]
    assert into_plan.count("plan-scaffold") == 1


def test_standalone_kit_has_no_wt_or_ownership(tmp_path):
    """Standalone kit is composed from the packaged definition (HATS-1043):
    frozen-integrity + scaffold/gate/stamp/clear — still no worktree/ownership."""
    names = {ext.name for ext in standalone_extensions(tmp_path / "tasks")}
    assert names == {
        "frozen-integrity",
        "plan-scaffold",
        "plan-gate",
        "stamp-lifecycle",
        "clear-lifecycle",
    }
    assert "worktree" not in names and "ownership" not in names


def test_full_stack_lifecycle_with_views(project, monkeypatch):
    """One walk through the whole wired stack on real git: scaffold → gate →
    worktree → merge → epicless done, with STATE.md tracking along."""
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="Full stack")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    plan_path = kernel.tasks_dir / "T-1" / "plan.md"
    assert plan_path.exists()  # scaffold wrote it
    plan_path.write_text(_FILLED_PLAN)

    kernel.transition("T-1", "execute", actor="test", caller_cwd=project)
    assert WorktreeManager.load_for_task(
        project, "T-1", state_dir=worktrees_dir(project)
    ) is not None

    for state in ("document", "review", "done"):
        kernel.transition("T-1", state, actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "done"

    state_md = (project / ".agent" / "STATE.md").read_text()
    assert "## DONE" in state_md and "T-1" in state_md
