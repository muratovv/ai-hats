"""Live parity: the rack kernel topology (backlog.yaml) ≡ the tracker mirror
(``TaskState.valid_transitions()``).

backlog.yaml declares itself an EXACT copy of ``valid_transitions`` (HATS-1042,
ADR-0017 §1); the rack cannot import the tracker (import-hygiene pin), so its
own ``test_fsm.py`` pins a hand-copied table. This integrator-level test is the
one place both SSOTs are importable — it fails loud the moment they drift, so a
one-sided edit (e.g. adding review→execute to only one, HATS-1052) cannot land.
"""

from __future__ import annotations

from ai_hats_rack.fsm import load_topology

from ai_hats.models import TaskState


def _tracker_edges() -> dict[str, tuple[str, ...]]:
    return {
        state.value: tuple(t.value for t in targets)
        for state, targets in TaskState.valid_transitions().items()
    }


def test_topology_equals_tracker_valid_transitions() -> None:
    assert dict(load_topology().edges) == _tracker_edges()


def test_review_to_execute_present_in_both_ssots() -> None:
    # HATS-1052: the rework edge must exist on BOTH sides, not just one.
    assert load_topology().allows("review", "execute")
    assert TaskState.REVIEW.can_transition_to(TaskState.EXECUTE)
