"""Kernel pins: single-persist atomicity, bare-kernel purity, dispatch
semantics, journal, lock model (incidents-doc §4 adapted to K1)."""

from __future__ import annotations

import pytest
from filelock import FileLock

from ai_hats_rack.dispatch import AbortOperation, Delta, OperationAborted, Phase
from ai_hats_rack.events import PreDestroyEvent
from ai_hats_rack.fsm import InvalidTransitionError, UnknownStateError
from ai_hats_rack.kernel import (
    ForceRequiresReasonError,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
)

from rack_testkit import CollectingSink, StubSubscriber, in_lock, make_kernel, post_lock, walk


def _create(kernel, cwd, task_id="T-1", **kwargs):
    return kernel.create(actor="test", caller_cwd=cwd, task_id=task_id, **kwargs).task


# ---------------------------------------------------------------------------
# Bare kernel = pure FSM (HATS-866/AC4 heir)
# ---------------------------------------------------------------------------


def test_bare_kernel_full_lifecycle(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd, title="pure fsm")
    walk(kernel, "T-1", "plan", "execute", "document", "review", "done", cwd=cwd)
    done = kernel.get("T-1")
    assert done.state == "done"
    assert done.completed_at
    # no subscriber ran, no subscriber traces in the log
    assert all("Worktree" not in e.message for e in done.work_log)


def test_create_uses_topology_initial(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    task = _create(kernel, cwd)
    assert task.state == "brainstorm"
    assert kernel.get("T-1").state == "brainstorm"


# ---------------------------------------------------------------------------
# FSM guard (self-documenting refusals, force semantics)
# ---------------------------------------------------------------------------


def test_invalid_transition_names_legal_edges_and_persists_nothing(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    before = (tasks_dir / "T-1" / "task.yaml").read_bytes()
    with pytest.raises(InvalidTransitionError) as exc_info:
        kernel.transition("T-1", "done", actor="test", caller_cwd=cwd)
    assert exc_info.value.allowed == ("plan", "blocked", "cancelled")
    assert (tasks_dir / "T-1" / "task.yaml").read_bytes() == before


def test_unknown_target_state_is_loud(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    with pytest.raises(UnknownStateError):
        kernel.transition("T-1", "shipping", actor="test", caller_cwd=cwd)


def test_force_requires_reason(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    with pytest.raises(ForceRequiresReasonError):
        kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd, force=True)


def test_force_relaxes_arrow_and_logs_reason(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    # plan → review is not an edge; force takes it, with the reason on record
    result = kernel.transition(
        "T-1", "review", actor="test", caller_cwd=cwd, force=True, reason="skip for test"
    )
    reloaded = kernel.get("T-1")
    assert reloaded.state == "review"
    assert any(
        "Forced transition plan → review: skip for test" in e.message
        for e in reloaded.work_log
    )
    assert result.journal[0].force is True
    assert result.journal[0].reason == "skip for test"


def test_force_same_state_refused(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    with pytest.raises(ValueError, match="already in state"):
        kernel.transition(
            "T-1", "brainstorm", actor="test", caller_cwd=cwd, force=True, reason="noop"
        )


def test_unknown_task(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    with pytest.raises(UnknownTaskError):
        kernel.transition("T-404", "plan", actor="test", caller_cwd=cwd)
    assert not (tasks_dir / "T-404").exists()  # no stray dir bootstrapped


def test_create_existing_id_refused(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    with pytest.raises(TaskExistsError):
        _create(kernel, cwd)


# ---------------------------------------------------------------------------
# Single persist / in-lock abort = zero bytes (HATS-723 / HATS-481 heirs)
# ---------------------------------------------------------------------------


def test_abort_in_lock_leaves_zero_bytes(tasks_dir, cwd):
    # HATS-723 heir: final_state must not half-apply when a gate aborts.
    gate = StubSubscriber(
        "gate",
        [in_lock("edge:plan--execute")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation("plan sections empty: Steps")),
    )
    kernel = make_kernel(tasks_dir, subscribers=[gate])
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    before = (tasks_dir / "T-1" / "task.yaml").read_bytes()

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition(
            "T-1", "execute", actor="test", caller_cwd=cwd, final_state="must not persist"
        )
    assert exc_info.value.reason == "plan sections empty: Steps"
    assert exc_info.value.subscriber == "gate"
    assert (tasks_dir / "T-1" / "task.yaml").read_bytes() == before  # zero bytes changed
    reloaded = kernel.get("T-1")
    assert reloaded.state == "plan"
    assert reloaded.final_state == ""


def test_raising_subscriber_aborts_before_persist(tasks_dir, cwd):
    # HATS-866/AC3 + HATS-481 heir: a raw exception propagates through the
    # seam and the DONE state is never persisted.
    class ExplodingTeardown:
        name = "worktree"

        def subscriptions(self):
            return [in_lock("edge:review--done")]

        def on_event(self, ctx):
            raise RuntimeError("merge failed")

    kernel = make_kernel(tasks_dir, subscribers=[ExplodingTeardown()])
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", "document", "review", cwd=cwd)
    with pytest.raises(RuntimeError, match="merge failed"):
        kernel.transition("T-1", "done", actor="test", caller_cwd=cwd)
    reloaded = kernel.get("T-1")
    assert reloaded.state == "review"
    assert reloaded.completed_at == ""


def test_delta_is_applied_and_persisted_last(tasks_dir, cwd):
    # HATS-866/AC5 heir: the card records what the subscriber did — via the
    # returned delta, inside the same single persist.
    wt = StubSubscriber(
        "worktree",
        [in_lock("edge:plan--execute")],
        action=lambda ctx: Delta(work_log=("Worktree: /tmp/wt-t-1",)),
    )
    kernel = make_kernel(tasks_dir, subscribers=[wt])
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", cwd=cwd)
    reloaded = kernel.get("T-1")
    assert any("Worktree: /tmp/wt-t-1" in e.message for e in reloaded.work_log)


def test_subscriber_state_is_immutable_copy(tasks_dir, cwd):
    def vandalize(ctx):
        ctx.task.title = "mutated"
        ctx.task.log_work("smuggled entry")
        return None

    sub = StubSubscriber("vandal", [in_lock("edge:brainstorm--plan")], action=vandalize)
    kernel = make_kernel(tasks_dir, subscribers=[sub])
    _create(kernel, cwd, title="original")
    walk(kernel, "T-1", "plan", cwd=cwd)
    assert kernel.get("T-1").title == "original"


# ---------------------------------------------------------------------------
# Dispatch order, context, phases
# ---------------------------------------------------------------------------


def test_priority_order_and_registration_tiebreak(tasks_dir, cwd):
    calls: list[str] = []

    def recorder(name):
        def action(ctx):
            calls.append(name)
            return None

        return action

    subs = [
        StubSubscriber("late", [in_lock("edge:brainstorm--plan", priority=200)], recorder("late")),
        StubSubscriber("first", [in_lock("edge:brainstorm--plan", priority=10)], recorder("first")),
        StubSubscriber("tie-a", [in_lock("edge:brainstorm--plan", priority=50)], recorder("tie-a")),
        StubSubscriber("tie-b", [in_lock("edge:brainstorm--plan", priority=50)], recorder("tie-b")),
    ]
    kernel = make_kernel(tasks_dir, subscribers=subs)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    assert calls == ["first", "tie-a", "tie-b", "late"]


def test_context_carries_caller_cwd_actor_force(tasks_dir, tmp_path):
    sub = StubSubscriber("probe", [in_lock("edge:brainstorm--plan")])
    kernel = make_kernel(tasks_dir, subscribers=[sub])
    op_cwd = tmp_path / "somewhere"
    op_cwd.mkdir()
    kernel.create(actor="session:abc", caller_cwd=op_cwd, task_id="T-1")
    kernel.transition("T-1", "plan", actor="session:abc", caller_cwd=op_cwd)
    ctx = sub.contexts[0]
    assert ctx.caller_cwd == op_cwd  # raw cwd threaded, never re-read (HATS-840)
    assert ctx.actor == "session:abc"
    assert ctx.force is False
    assert ctx.task.id == "T-1"


def test_is_epic_recomputed_on_every_dispatch(tasks_dir, cwd):
    # HATS-794/977/979 heir: category is a per-dispatch predicate from the
    # CURRENT child-set, never frozen at claim time.
    sub = StubSubscriber("probe", [in_lock("edge:brainstorm--plan"), in_lock("edge:plan--execute")])
    kernel = make_kernel(tasks_dir, subscribers=[sub])
    _create(kernel, cwd, title="parent")
    walk(kernel, "T-1", "plan", cwd=cwd)
    assert sub.contexts[-1].is_epic is False

    kernel.create(actor="test", caller_cwd=cwd, task_id="T-2", parent_task="T-1")
    walk(kernel, "T-1", "execute", cwd=cwd)
    assert sub.contexts[-1].is_epic is True


def test_post_lock_runs_after_persist_and_release(tasks_dir, cwd):
    seen: dict[str, object] = {}

    def reaction(ctx):
        # the persist already happened: disk shows the new state
        seen["disk_state"] = (tasks_dir / "T-1" / "task.yaml").read_text()
        # and the task lock is released: it can be re-acquired immediately
        with FileLock(str(tasks_dir / "T-1" / ".lock"), timeout=0.5):
            seen["lock_free"] = True
        return None

    sub = StubSubscriber("reactor", [post_lock("edge:brainstorm--plan")], action=reaction)
    kernel = make_kernel(tasks_dir, subscribers=[sub])
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    assert "state: plan" in seen["disk_state"]
    assert seen["lock_free"] is True


def test_post_lock_error_reported_not_raised(tasks_dir, cwd):
    def explode(ctx):
        raise RuntimeError("view regen failed")

    sub = StubSubscriber("views", [post_lock("edge:brainstorm--plan")], action=explode)
    kernel = make_kernel(tasks_dir, subscribers=[sub])
    _create(kernel, cwd)
    result = kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    assert kernel.get("T-1").state == "plan"  # the transition itself stands
    outcomes = result.journal[0].outcomes
    assert [o.outcome for o in outcomes] == ["error"]
    assert "view regen failed" in outcomes[0].reason
    assert outcomes[0].phase is Phase.POST_LOCK


# ---------------------------------------------------------------------------
# Journal + sink seam (K7)
# ---------------------------------------------------------------------------


def test_journal_records_every_subscriber_outcome(tasks_dir, cwd):
    ok = StubSubscriber("gate", [in_lock("edge:brainstorm--plan", priority=1)])
    delta = StubSubscriber(
        "wt",
        [in_lock("edge:brainstorm--plan", priority=2)],
        action=lambda ctx: Delta(work_log=("note",)),
    )
    reactor = StubSubscriber("epic", [post_lock("edge:brainstorm--plan")])
    kernel = make_kernel(tasks_dir, subscribers=[ok, delta, reactor])
    _create(kernel, cwd)
    result = kernel.transition("T-1", "plan", actor="me", caller_cwd=cwd)

    assert len(result.journal) == 1
    record = result.journal[0]
    assert record.event_key == "edge:brainstorm--plan"
    assert record.task_id == "T-1"
    assert record.actor == "me"
    assert [(o.subscriber, o.outcome) for o in record.outcomes] == [
        ("gate", "ok"),
        ("wt", "delta"),
        ("epic", "ok"),
    ]
    assert record.outcomes[1].delta.work_log == ("note",)


def test_sink_receives_aborted_dispatch(tasks_dir, cwd):
    # Refusals stay auditable (PROP-004 direction): the sink sees the abort.
    sink = CollectingSink()
    gate = StubSubscriber(
        "gate",
        [in_lock("edge:brainstorm--plan")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation("nope")),
    )
    kernel = make_kernel(tasks_dir, subscribers=[gate], journal_sink=sink)
    _create(kernel, cwd)
    with pytest.raises(OperationAborted):
        kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    assert len(sink.records) == 1
    assert [o.outcome for o in sink.records[0].outcomes] == ["abort"]
    assert sink.records[0].outcomes[0].reason == "nope"


def test_sink_receives_successful_dispatch(tasks_dir, cwd):
    sink = CollectingSink()
    kernel = make_kernel(tasks_dir, journal_sink=sink)
    _create(kernel, cwd)
    kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    assert [r.event_key for r in sink.records] == ["edge:brainstorm--plan"]
    assert sink.records[0].outcomes == ()  # bare kernel: dispatch still journaled


def test_transitions_delta_list_in_result(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    result = kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    assert len(result.transitions) == 1
    t = result.transitions[0]
    assert (t.task_id, t.from_state, t.to_state) == ("T-1", "brainstorm", "plan")


# ---------------------------------------------------------------------------
# Anchor-field lifecycle (format parity)
# ---------------------------------------------------------------------------


def test_done_stamps_completed_at_and_reopen_clears_it(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", "document", "review", "done", cwd=cwd)
    assert kernel.get("T-1").completed_at

    kernel.transition("T-1", "execute", actor="test", caller_cwd=cwd)  # reopen (HATS-328)
    reloaded = kernel.get("T-1")
    assert reloaded.completed_at == ""
    assert any("Reopened from done" in e.message for e in reloaded.work_log)


def test_cancelled_stamps_completed_at(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    kernel.transition("T-1", "cancelled", actor="test", caller_cwd=cwd, resolution="wontfix")
    reloaded = kernel.get("T-1")
    assert reloaded.completed_at
    assert reloaded.resolution == "wontfix"


def test_resolution_and_final_state_ride_the_lock_window(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", "document", cwd=cwd)
    kernel.transition(
        "T-1", "review", actor="test", caller_cwd=cwd, final_state="did the thing"
    )
    assert kernel.get("T-1").final_state == "did the thing"


# ---------------------------------------------------------------------------
# Epicify event (category change is a first-class event)
# ---------------------------------------------------------------------------


def test_create_child_dispatches_epicify(tasks_dir, cwd):
    reconciler = StubSubscriber("ownership-reconcile", [post_lock("epicify")])
    kernel = make_kernel(tasks_dir, subscribers=[reconciler])
    _create(kernel, cwd, title="future epic")
    result = kernel.create(actor="test", caller_cwd=cwd, task_id="T-2", parent_task="T-1")

    assert len(reconciler.contexts) == 1
    ctx = reconciler.contexts[0]
    assert ctx.task.id == "T-1"  # the handler sees the EPIC, not the child
    assert ctx.is_epic is True
    assert ctx.event.child_id == "T-2"
    assert [r.event_key for r in result.journal] == ["epicify"]


def test_set_parent_dispatches_epicify(tasks_dir, cwd):
    reconciler = StubSubscriber("wt-reconcile", [post_lock("epicify")])
    kernel = make_kernel(tasks_dir, subscribers=[reconciler])
    _create(kernel, cwd)
    _create(kernel, cwd, task_id="T-2")
    result = kernel.set_parent("T-2", "T-1", actor="test", caller_cwd=cwd)
    assert kernel.get("T-2").parent_task == "T-1"
    assert [r.event_key for r in result.journal] == ["epicify"]
    assert reconciler.contexts[0].event.epic_id == "T-1"


def test_create_with_dangling_parent_skips_epicify(tasks_dir, cwd):
    reconciler = StubSubscriber("reconcile", [post_lock("epicify")])
    kernel = make_kernel(tasks_dir, subscribers=[reconciler])
    result = kernel.create(actor="test", caller_cwd=cwd, task_id="T-2", parent_task="T-404")
    assert result.journal == ()
    assert reconciler.contexts == []


def test_self_parent_refused(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    with pytest.raises(ValueError, match="own parent"):
        kernel.set_parent("T-1", "T-1", actor="test", caller_cwd=cwd)


# ---------------------------------------------------------------------------
# Pre-destroy publish contract (K3 consumers)
# ---------------------------------------------------------------------------


def test_publish_pre_destroy_can_abort(tasks_dir, cwd):
    guard = StubSubscriber(
        "review-notes-guard",
        [in_lock("pre-destroy")],
        action=lambda ctx: (_ for _ in ()).throw(AbortOperation("pending hunk review")),
    )
    sink = CollectingSink()
    kernel = make_kernel(tasks_dir, subscribers=[guard], journal_sink=sink)
    _create(kernel, cwd)
    with pytest.raises(OperationAborted, match="pending hunk review"):
        kernel.publish(
            PreDestroyEvent(operation="worktree-discard", task_id="T-1"),
            actor="test",
            caller_cwd=cwd,
        )
    assert [o.outcome for o in sink.records[0].outcomes] == ["abort"]


def test_publish_pre_destroy_ok_returns_journal(tasks_dir, cwd):
    seen = StubSubscriber("extractor", [in_lock("pre-destroy")])
    kernel = make_kernel(tasks_dir, subscribers=[seen])
    _create(kernel, cwd)
    records = kernel.publish(
        PreDestroyEvent(operation="worktree-merge", task_id="T-1"),
        actor="test",
        caller_cwd=cwd,
    )
    assert [o.outcome for o in records[0].outcomes] == ["ok"]
    assert seen.contexts[0].event.operation == "worktree-merge"


# ---------------------------------------------------------------------------
# Lock model
# ---------------------------------------------------------------------------


def test_task_lock_timeout_is_loud(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, lock_timeout=0.2)
    _create(kernel, cwd)
    holder = FileLock(str(tasks_dir / "T-1" / ".lock"))
    with holder:
        with pytest.raises(LockTimeoutError, match="stuck rack process"):
            kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    # holder released → the same call goes through
    kernel.transition("T-1", "plan", actor="test", caller_cwd=cwd)
    assert kernel.get("T-1").state == "plan"


def test_alloc_lock_timeout_is_loud(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir, lock_timeout=0.2)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    holder = FileLock(str(tasks_dir / ".alloc.lock"))
    with holder:
        with pytest.raises(LockTimeoutError, match="task-id allocation"):
            kernel.create(actor="test", caller_cwd=cwd, title="blocked")


def test_sequential_ids_allocated(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    first = kernel.create(actor="test", caller_cwd=cwd, title="a").task
    second = kernel.create(actor="test", caller_cwd=cwd, title="b").task
    assert (first.id, second.id) == ("T-001", "T-002")


# ---------------------------------------------------------------------------
# log_work
# ---------------------------------------------------------------------------


def test_log_work_appends_with_actor(tasks_dir, cwd):
    kernel = make_kernel(tasks_dir)
    _create(kernel, cwd)
    kernel.log_work("T-1", "made progress", actor="session:s1")
    entries = kernel.get("T-1").work_log
    assert entries[-1].message == "[session:s1] made progress"
