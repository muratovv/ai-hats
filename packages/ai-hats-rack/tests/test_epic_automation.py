"""Ported group 9 (incidents §4): child-driven epic automation without silent
no-ops (HATS-688/690/692/789) + the explicit coverage table (source state ×
child trigger → outcome) that forbids the HATS-692 stranding class."""

from __future__ import annotations

import pytest

from ai_hats_rack.extensions import AUTOMATION_ACTOR, EpicAutomationExtension, decide
from ai_hats_rack.fsm import load_topology

from rack_testkit import CollectingSink, make_kernel, walk

TOPOLOGY = load_topology()


@pytest.fixture
def kernel(tasks_dir):
    sink = CollectingSink()
    automation = EpicAutomationExtension(topology=TOPOLOGY)
    k = make_kernel(tasks_dir, topology=TOPOLOGY, subscribers=[automation], journal_sink=sink)
    automation.bind(k)
    k.sink = sink  # test-only handle
    return k


def _create(kernel, cwd, task_id, title="t", parent=""):
    return kernel.create(
        actor="test", caller_cwd=cwd, task_id=task_id, title=title, parent_task=parent
    ).task


def _walk_to_done(kernel, task_id, cwd):
    walk(kernel, task_id, "plan", "execute", "document", "review", "done", cwd=cwd)


def _fast_close(kernel, task_id, cwd):
    """The close_task analogue: forced brainstorm/plan → done."""
    return kernel.transition(
        task_id, "done", actor="test", caller_cwd=cwd, force=True, reason="shipped on master"
    )


def _epic_in(kernel, epic_id, state, cwd):
    _create(kernel, cwd, epic_id, title="Epic")
    hops = {"plan": ["plan"], "execute": ["plan", "execute"],
            "document": ["plan", "execute", "document"]}
    walk(kernel, epic_id, *hops[state], cwd=cwd)


# ---------------------------------------------------------------------------
# Advance (HATS-690 Q2/Q2a)
# ---------------------------------------------------------------------------


def test_epic_auto_advances_to_review_when_all_children_done(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "execute"  # C2 still open

    _walk_to_done(kernel, "C2", cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "review"
    assert any("Auto-advanced" in e.message for e in epic.work_log)


def test_advance_outcome_rides_the_child_journal(kernel, cwd):
    """The journal answers "why did the epic move" (K1 design decision)."""
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    walk(kernel, "C1", "plan", "execute", "document", "review", cwd=cwd)
    result = kernel.transition("C1", "done", actor="test", caller_cwd=cwd)

    outcomes = {o.subscriber: o for o in result.journal[0].outcomes}
    delta = outcomes["epic-automation"].delta
    assert delta is not None and "advance execute -> review" in delta.work_log[0]
    # Each epic hop is its own journaled dispatch under the automation actor.
    auto_records = [r for r in kernel.sink.records if r.actor == AUTOMATION_ACTOR]
    assert [r.event_key for r in auto_records] == [
        "edge:execute--document",
        "edge:document--review",
    ]


def test_epic_auto_advances_from_document_single_hop(kernel, cwd):
    _epic_in(kernel, "EPIC", "document", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"


def test_cancelled_child_does_not_block_advance(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    kernel.transition("C2", "cancelled", actor="test", caller_cwd=cwd, resolution="dropped")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"


def test_failed_child_blocks_advance(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    walk(kernel, "C2", "plan", "execute", "failed", cwd=cwd)
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "execute"  # not advanced


def test_blocked_child_blocks_advance(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    walk(kernel, "C2", "blocked", cwd=cwd)
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "execute"


def test_advance_requires_at_least_one_done(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    kernel.transition("C1", "cancelled", actor="test", caller_cwd=cwd, resolution="drop")
    kernel.transition("C2", "cancelled", actor="test", caller_cwd=cwd, resolution="drop")
    assert kernel.get("EPIC").state == "execute"  # all cancelled, none done


def test_zero_children_epic_not_advanced(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "SOLO")  # unrelated
    _walk_to_done(kernel, "SOLO", cwd)
    assert kernel.get("EPIC").state == "execute"


# ---------------------------------------------------------------------------
# Activation + fast-close fallback (HATS-692 / HATS-789)
# ---------------------------------------------------------------------------


def test_child_taken_activates_plan_epic(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    walk(kernel, "C1", "plan", "execute", cwd=cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "execute"
    assert any("Auto-activated plan -> execute" in e.message for e in epic.work_log)


def test_activation_is_idempotent(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    walk(kernel, "C1", "plan", "execute", cwd=cwd)
    assert kernel.get("EPIC").state == "execute"
    log_len = len(kernel.get("EPIC").work_log)

    walk(kernel, "C1", "document", cwd=cwd)  # epic already execute → no re-fire
    assert kernel.get("EPIC").state == "execute"
    assert len(kernel.get("EPIC").work_log) == log_len


def test_brainstorm_epic_activated(kernel, cwd):
    """HATS-789: an active child proves decomposition — brainstorm epics
    activate via a brainstorm → plan → execute multi-hop."""
    _create(kernel, cwd, "EPIC", title="Epic")  # stays brainstorm
    _create(kernel, cwd, "C1", parent="EPIC")
    walk(kernel, "C1", "plan", "execute", cwd=cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "execute"
    assert any("Auto-activated brainstorm -> execute" in e.message for e in epic.work_log)


def test_brainstorm_epic_advances_on_completion(kernel, cwd):
    """HATS-789: activation on the execute hop, advance to review on done."""
    _create(kernel, cwd, "EPIC", title="Epic")
    _create(kernel, cwd, "C1", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"


def test_plan_epic_fast_close_advances_to_review(kernel, cwd):
    """The HATS-688 stranding bug: children fast-closed while the epic sat in
    plan used to strand it forever."""
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    _fast_close(kernel, "C1", cwd)
    _fast_close(kernel, "C2", cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "review"
    assert any("Auto-advanced plan -> review" in e.message for e in epic.work_log)


def test_brainstorm_epic_fast_close_advances_to_review(kernel, cwd):
    _create(kernel, cwd, "EPIC", title="Epic")
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    _fast_close(kernel, "C1", cwd)
    _fast_close(kernel, "C2", cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "review"
    assert any("Auto-advanced brainstorm -> review" in e.message for e in epic.work_log)


def test_plan_epic_fast_close_mixed_done_cancelled_advances(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    _fast_close(kernel, "C1", cwd)
    kernel.transition("C2", "cancelled", actor="test", caller_cwd=cwd, resolution="drop")
    assert kernel.get("EPIC").state == "review"  # >=1 done holds


def test_plan_epic_all_cancelled_not_advanced(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _create(kernel, cwd, "C2", parent="EPIC")
    kernel.transition("C1", "cancelled", actor="test", caller_cwd=cwd, resolution="drop")
    kernel.transition("C2", "cancelled", actor="test", caller_cwd=cwd, resolution="drop")
    assert kernel.get("EPIC").state == "plan"


def test_create_brainstorm_child_does_not_activate_plan_epic(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")  # brainstorm child, no work yet
    assert kernel.get("EPIC").state == "plan"


def test_reparent_active_child_into_plan_epic_activates(kernel, cwd):
    _epic_in(kernel, "EPIC", "plan", cwd)
    _create(kernel, cwd, "FREE")
    walk(kernel, "FREE", "plan", "execute", cwd=cwd)
    kernel.set_parent("FREE", "EPIC", actor="test", caller_cwd=cwd)
    assert kernel.get("EPIC").state == "execute"


def test_reparent_active_child_into_brainstorm_epic_activates(kernel, cwd):
    _create(kernel, cwd, "EPIC", title="Epic")  # brainstorm
    _create(kernel, cwd, "FREE")
    walk(kernel, "FREE", "plan", "execute", cwd=cwd)
    kernel.set_parent("FREE", "EPIC", actor="test", caller_cwd=cwd)
    epic = kernel.get("EPIC")
    assert epic.state == "execute"
    assert any("Auto-activated brainstorm -> execute" in e.message for e in epic.work_log)


# ---------------------------------------------------------------------------
# Reopen (HATS-690 Q3)
# ---------------------------------------------------------------------------


def _epic_done_with_child(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"
    kernel.transition("EPIC", "done", actor="test", caller_cwd=cwd)
    assert kernel.get("EPIC").completed_at != ""


def test_create_under_done_epic_reopens(kernel, cwd):
    _epic_done_with_child(kernel, cwd)
    _create(kernel, cwd, "C2", parent="EPIC")  # live child under a done epic
    epic = kernel.get("EPIC")
    assert epic.state == "execute"
    assert epic.completed_at == ""
    assert any("Auto-reopened" in e.message for e in epic.work_log)


def test_reparent_into_done_epic_reopens(kernel, cwd):
    _epic_done_with_child(kernel, cwd)
    _create(kernel, cwd, "FREE")
    kernel.set_parent("FREE", "EPIC", actor="test", caller_cwd=cwd)
    assert kernel.get("EPIC").state == "execute"


def test_child_reopen_done_to_execute_reopens_epic(kernel, cwd):
    _epic_done_with_child(kernel, cwd)
    kernel.transition("C1", "execute", actor="test", caller_cwd=cwd)  # child reopened
    assert kernel.get("EPIC").state == "execute"


def test_fast_close_child_advances_epic(kernel, cwd):
    """HATS-690 D2: a child fast-closed to done completes its epic."""
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _fast_close(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"


def test_epic_already_in_review_is_noop(kernel, cwd):
    _epic_in(kernel, "EPIC", "execute", cwd)
    _create(kernel, cwd, "C1", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"
    log_len = len(kernel.get("EPIC").work_log)

    _create(kernel, cwd, "C2", parent="EPIC")
    _fast_close(kernel, "C2", cwd)
    assert kernel.get("EPIC").state == "review"
    assert len(kernel.get("EPIC").work_log) == log_len


def test_no_grandparent_cascade(kernel, cwd):
    """Automation-driven epic hops carry the automation actor and are ignored
    on re-entry — a completed epic never completes its own parent."""
    _create(kernel, cwd, "GRAND", title="Grandparent")
    _create(kernel, cwd, "EPIC", title="Epic", parent="GRAND")
    _create(kernel, cwd, "C1", parent="EPIC")
    _walk_to_done(kernel, "C1", cwd)
    assert kernel.get("EPIC").state == "review"  # advanced by its child
    assert kernel.get("GRAND").state == "brainstorm"  # untouched


def test_dangling_parent_ref_is_noop(kernel, cwd):
    _create(kernel, cwd, "C1", parent="GHOST-1")  # parent never existed
    walk(kernel, "C1", "plan", "execute", cwd=cwd)  # must not raise
    assert kernel.get("C1").state == "execute"


# ---------------------------------------------------------------------------
# The coverage table: every epic source state × child trigger → explicit
# outcome. Adding a state to fsm.yaml without extending the table fails here
# (silent holes are the HATS-692 class).
# ---------------------------------------------------------------------------

# trigger id → (just-mutated child state, full child-set states)
TRIGGERS = {
    "child_active": ("execute", ["execute"]),
    "child_open": ("brainstorm", ["brainstorm"]),
    "child_blocked": ("blocked", ["blocked", "done"]),
    "child_failed": ("failed", ["failed", "done"]),
    "all_resolved_done": ("done", ["done"]),
    "all_resolved_mixed": ("cancelled", ["done", "cancelled"]),
    "all_resolved_cancelled_only": ("cancelled", ["cancelled"]),
}

EXPECTED: dict[str, dict[str, tuple[str, str] | None]] = {
    "brainstorm": {
        "child_active": ("activate", "execute"),
        "child_open": None,
        "child_blocked": None,
        "child_failed": None,
        "all_resolved_done": ("advance", "review"),
        "all_resolved_mixed": ("advance", "review"),
        "all_resolved_cancelled_only": None,
    },
    "plan": {
        "child_active": ("activate", "execute"),
        "child_open": None,
        "child_blocked": None,
        "child_failed": None,
        "all_resolved_done": ("advance", "review"),
        "all_resolved_mixed": ("advance", "review"),
        "all_resolved_cancelled_only": None,
    },
    "execute": {
        "child_active": None,
        "child_open": None,
        "child_blocked": None,
        "child_failed": None,
        "all_resolved_done": ("advance", "review"),
        "all_resolved_mixed": ("advance", "review"),
        "all_resolved_cancelled_only": None,
    },
    "document": {
        "child_active": None,
        "child_open": None,
        "child_blocked": None,
        "child_failed": None,
        "all_resolved_done": ("advance", "review"),
        "all_resolved_mixed": ("advance", "review"),
        "all_resolved_cancelled_only": None,
    },
    "review": {t: None for t in TRIGGERS},
    "done": {
        "child_active": ("reopen", "execute"),
        "child_open": ("reopen", "execute"),
        "child_blocked": ("reopen", "execute"),
        "child_failed": ("reopen", "execute"),
        "all_resolved_done": None,
        "all_resolved_mixed": None,
        "all_resolved_cancelled_only": None,
    },
    "blocked": {t: None for t in TRIGGERS},
    "failed": {t: None for t in TRIGGERS},
    "cancelled": {t: None for t in TRIGGERS},
}


def test_coverage_table_spans_the_full_topology():
    assert set(EXPECTED) == set(TOPOLOGY.states), (
        "the epic-automation coverage table must name every FSM state explicitly"
    )
    for state, row in EXPECTED.items():
        assert set(row) == set(TRIGGERS), f"incomplete trigger row for {state}"


@pytest.mark.parametrize("epic_state", sorted(EXPECTED))
@pytest.mark.parametrize("trigger", sorted(TRIGGERS))
def test_decision_table(epic_state, trigger):
    child_state, child_states = TRIGGERS[trigger]
    assert decide(epic_state, child_state, child_states) == EXPECTED[epic_state][trigger]


# ---------------------------------------------------------------------------
# requires_states — the full decision vocabulary, validated fail-closed (R8)
# ---------------------------------------------------------------------------


def test_requires_states_is_the_full_decision_vocabulary():
    ext = EpicAutomationExtension(topology=TOPOLOGY)
    assert ext.requires_states() == frozenset(
        {"brainstorm", "plan", "execute", "document", "review", "done", "cancelled"}
    )
    assert ext.requires_states() <= set(TOPOLOGY.states)  # packaged topology covers it


def test_composition_refuses_a_topology_missing_the_vocabulary():
    # A topology without `review` would strand an advancing epic — the HATS-692
    # class, caught at composition, not only by the decision-table test.
    from types import MappingProxyType

    from ai_hats_rack.dispatch import RequiresStatesError, validate_requires_states
    from ai_hats_rack.fsm import Topology

    states = tuple(s for s in TOPOLOGY.states if s != "review")
    truncated = Topology(
        initial="brainstorm", states=states, edges=MappingProxyType({s: () for s in states})
    )
    ext = EpicAutomationExtension(topology=TOPOLOGY)
    with pytest.raises(RequiresStatesError, match="epic-automation") as exc_info:
        validate_requires_states([ext], truncated, source="truncated")
    assert "review" in exc_info.value.missing
