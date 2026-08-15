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
from ai_hats_rack.dispatch import AbortOperation, DispatchContext, Phase
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.extensions.epic import AUTOMATION_ACTOR
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


def _be_session(monkeypatch, session_id: str, project: Path, role: str = "maintainer") -> None:
    """Stand in for a session the way a launch writes one — envelope included.

    A bare ``AI_HATS_SESSION_ID`` stands in for a session no launch produces
    (HATS-1594), and ownership refuses it rather than reading it as absence.
    The ``role`` is load-bearing since HATS-1682: consent points and check
    bindings are both role properties, so which one the session claims decides
    what the kernel arms.
    """
    from ai_hats.session_identity import SessionIdentity

    identity = SessionIdentity(
        id=session_id,
        role=role,
        provider="claude",
        project_dir=project,
        session_dir=project / ".agent" / "runs" / f"session_{session_id}",
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)


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
        backlog_owner=project,
        tasks_dir=project / ".agent" / "tasks",
        state_md_path=project / ".agent" / "STATE.md",
        prefix="T",
        **kwargs,
    )


def _check_pack(project: Path, script: Path | None = None):
    """The consumer pack with its resolution stubbed to one binding — this file
    pins the wiring, not the two-mode resolver (``test_rack_consumers``)."""
    from ai_hats_core import ResolvedCheck
    from ai_hats_rack.definition import resolve_definition

    from ai_hats_rack.checks import CheckSubscriber

    from ai_hats.rack_consumers import AiHatsCheckPort

    checks = ()
    if script is not None:
        checks = (
            ResolvedCheck(
                app="rack",
                path=("tasks",),
                run=f"quality::gates/{script.name}",
                at=("edge:plan--execute",),
                cargo={},
                on_error="refuse",
                script_path=script,
                declared_by="maintainer",
            ),
        )
    tasks_dir = project / ".agent" / "tasks"
    return [
        CheckSubscriber(
            AiHatsCheckPort(project, catalog=tasks_dir, resolve=lambda: checks),
            topology=resolve_definition(tasks_dir, prefix_alias="T", project_dir=project).topology,
            backlog=resolve_definition(tasks_dir, prefix_alias="T", project_dir=project).name,
        )
    ]


class _Sink:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def test_gate_abort_leaves_no_ownership_and_no_worktree(project, monkeypatch):
    """Ratification of fix #1 with the real extensions: the plan-gate fires
    before ownership claim and worktree setup, so its abort leaves zero
    side effects — no registry record, no worktree, zero bytes on the card."""
    _be_session(monkeypatch, "sess-a", project)
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
    assert (
        WorktreeManager.load_for_task(project, "T-1", state_dir=worktrees_dir(project)) is None
    ), "gate abort must not leave a worktree"
    assert not WorktreeManager.branch_exists(project, "task/t-1")
    assert (kernel.tasks_dir / "T-1" / "task.yaml").read_bytes() == before

    # PROP-004: the refusal itself is auditable — journaled, not swallowed.
    refusal = sink.records[-1]
    assert refusal.event_key == "edge:plan--execute"
    outcomes = {o.subscriber: o.outcome for o in refusal.outcomes}
    assert outcomes["plan-gate"] == "abort"
    assert "ownership" not in outcomes, "claim must not have run after the gate abort"


def test_ownership_follows_the_backlog_while_worktrees_follow_the_anchor(tmp_path):
    """HATS-1573: the asymmetry is deliberate, and pinned so it stays deliberate.

    Who owns a card is a fact about the BACKLOG; where its code is checked out
    is a fact about the CHECKOUT. An explicit --tasks-dir puts those in two
    different projects, and each side must stay where it belongs.
    """
    anchor = tmp_path / "anchor"
    (anchor / ".agent").mkdir(parents=True)
    backlog = tmp_path / "sbx"
    tasks_dir = backlog / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    tasks_dir.mkdir(parents=True)

    kernel = build_rack_kernel(
        anchor,
        backlog_owner=backlog,
        tasks_dir=tasks_dir,
        state_md_path=backlog / "STATE.md",
        prefix="T",
    )
    on_execute = kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    claim = next(s for s in on_execute if s.name == "ownership")
    worktree = next(s for s in on_execute if s.name == "worktree")

    assert claim.registry_path == tasks_dir.parent / "ownership.json"  # backlog side
    assert worktree.project_dir == anchor  # checkout side
    assert worktrees_dir(anchor).is_relative_to(anchor)


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
        # HATS-1682: the integrator's own, at the slot `plan-consent` held while
        # the packaged backlog declared it. Which edges it fires on is the role's
        # declaration now, so it subscribes to all of them and filters on dispatch.
        "consent",
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
        # HATS-1682: `consent` sits on THIS edge too — the role declares the two
        # roads into master, and its silence here was a merge nobody was asked
        # about. Ahead of the worktree teardown, so a refusal leaves no merge.
        "consent",
        "stamp-lifecycle",
        "worktree",
        "ownership-release",
    ]

    epicify = [s.name for s in kernel._dispatcher.subscribers_for("epicify", Phase.POST_LOCK)]
    assert epicify == ["ownership-release", "worktree", "epic-automation", "derived-views"]


def test_check_runner_takes_the_reserved_hook_slot(project):
    """HATS-1141: the checks runner books priority 15 — after the plan-gate,
    before the ownership claim and the worktree, so a refusal costs nothing."""
    kernel = _kernel(project, extra_subscribers=_check_pack(project))
    into_execute = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    ]
    assert into_execute == [
        "ownership-single-slot",
        "frozen-integrity",
        "plan-gate",
        "consent",
        "checks",
        "ownership",
        "worktree",
    ]


def test_check_refusal_leaves_no_ownership_and_no_worktree(project, monkeypatch):
    """R2 with the REAL extensions: slot 15 sits before the claim, so a refused
    check leaves no registry record, no worktree and an unchanged card."""
    # `consent` (11) sits ahead of `checks` (15) on this edge, and a role that
    # composes `trait-agent` declares `plan → execute` — so the check would be
    # unreachable behind an unanswered question. `session-reviewer` declares no
    # consent point, which keeps this test about the ORDER it was written for.
    # It stopped mattering only because the root conftest used to pre-grant the
    # env channel to every test in the repository (HATS-1682 fix 5).
    _be_session(monkeypatch, "sess-a", project, role="session-reviewer")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    script = project / "gate.sh"
    script.write_text("#!/bin/sh\necho 'plan not signed off'\nexit 2\n")
    script.chmod(0o755)

    kernel = _kernel(project, extra_subscribers=_check_pack(project, script))
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    before = (kernel.tasks_dir / "T-1" / "task.yaml").read_bytes()

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "checks"
    assert exc_info.value.reason == "plan not signed off"
    assert not (kernel.tasks_dir.parent / "ownership.json").exists()
    assert WorktreeManager.load_for_task(project, "T-1", state_dir=worktrees_dir(project)) is None
    assert (kernel.tasks_dir / "T-1" / "task.yaml").read_bytes() == before


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
    # `plan-consent` is NOT here since HATS-1682: the packaged definition stopped
    # declaring it, and the rack composes only what a definition declares.
    assert names == {
        "frozen-integrity",
        "plan-scaffold",
        "plan-gate",
        "stamp-lifecycle",
        "clear-lifecycle",
    }
    assert "worktree" not in names and "ownership" not in names


def test_stock_plan_catalog_wiring_without_consumer_config(project):
    """HATS-1160: the consumer plan_sections channel is gone. A kernel built
    with sections=None (the default) scaffolds AND gates on the stock
    DEFAULT_PLAN_SECTIONS catalog — the fallback lives in stock_factories, not
    a consumer read. Re-homed here from test_rack_consumers.py by HATS-1147:
    the guard is about the rack's stock wiring, and only ever touched the
    lifecycle channel incidentally."""
    kernel = _kernel(project)  # sections=None
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)

    scaffold = (kernel.tasks_dir / "T-1" / "plan.md").read_text()
    assert "## Requirements" in scaffold
    assert "## Verification Protocol" in scaffold

    # the gate enforces the stock catalog: a plan missing required sections aborts
    (kernel.tasks_dir / "T-1" / "plan.md").write_text("# Plan\n\n## Requirements\nonly this\n")
    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)
    assert exc_info.value.subscriber == "plan-gate"

    # a complete stock plan passes
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    kernel.transition("T-1", "execute", actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "execute"


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
    assert (
        WorktreeManager.load_for_task(project, "T-1", state_dir=worktrees_dir(project)) is not None
    )

    for state in ("document", "review", "done"):
        kernel.transition("T-1", state, actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "done"

    state_md = (project / ".agent" / "STATE.md").read_text()
    assert "## DONE" in state_md and "T-1" in state_md


# --- consent is a property of the edge, not of the command line (HATS-1682) ---


@pytest.fixture
def reviewed(project, monkeypatch):
    """A card parked in ``review`` under a role that declares the roads into master.

    `assistant` composes `trait-agent`, so it declares consent on
    `plan → execute` and `review → done`. The card is FORCED from `plan`
    straight to `review`: walking `plan → execute` is itself a declared point,
    and granting a consent here to set up a consent test is how a fixture ends
    up proving nothing. That the forced `plan → review` passes is the control —
    the gate matches declared EDGES, and force did not become a blanket refusal.
    """  # comment-length: allow — why the setup edge is forced and what it proves
    _be_session(monkeypatch, "sess-consent", project, role="assistant")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))

    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="into master")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    kernel.transition("T-1", "review", actor="test", caller_cwd=project, force=True, reason="park")
    assert kernel.get("T-1").state == "review", "the undeclared setup edge was gated"
    return kernel


def test_the_edge_into_master_is_refused_when_nobody_answered(reviewed, project):
    """Control for everything below: unanswered, the move does not happen."""
    with pytest.raises(OperationAborted) as exc_info:
        reviewed.transition("T-1", "done", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "consent"
    assert reviewed.get("T-1").state == "review"


def test_force_does_not_switch_consent_off(reviewed, project):
    """The incident this card was filed for.

    `--force` relaxes the FSM arrow; consent is not a property of the command,
    so nothing ADDED to the command can remove it — `consent | op --force`.
    The documented `close` recipe keeps working, it just asks once.
    """
    with pytest.raises(OperationAborted) as exc_info:
        reviewed.transition(
            "T-1", "done", actor="test", caller_cwd=project, force=True, reason="close"
        )

    assert exc_info.value.subscriber == "consent"
    assert reviewed.get("T-1").state == "review"


def test_the_card_and_the_actor_stay_exempt_while_the_command_line_does_not(reviewed, project):
    """The asymmetry, in one place: an epic and the epic automation answer to
    nobody, so they pass; `force` is an addition to the command, so it does not.
    """
    consent = next(
        s
        for s in reviewed._dispatcher.subscribers_for("edge:review--done", Phase.IN_LOCK)
        if s.name == "consent"
    )

    def ctx(**over):
        base = dict(
            event=EdgeEvent("review", "done"),
            task=reviewed.get("T-1"),
            caller_cwd=project,
            is_epic=False,
            actor="test",
        )
        return DispatchContext(**{**base, **over})

    assert consent.on_event(ctx(is_epic=True)) is None
    assert consent.on_event(ctx(actor=AUTOMATION_ACTOR)) is None
    with pytest.raises(AbortOperation):
        consent.on_event(ctx(force=True))


def test_plan_to_execute_is_refused_when_nobody_answered(project, monkeypatch):
    """The other declared point, and the tripwire for the conftest grant.

    While the root conftest handed `AI_HATS_PLAN_ACK=1` to EVERY test, this
    assertion could not fail: the env channel was pre-granted for the whole
    suite, so nothing about `plan → execute` consent was testable (HATS-1682).
    """
    _be_session(monkeypatch, "sess-plan", project, role="assistant")
    monkeypatch.setenv("AI_HATS_ROOT_PID", str(os.getpid()))
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "consent"
    assert kernel.get("T-1").state == "plan"
    assert not WorktreeManager.branch_exists(project, "task/t-1")
