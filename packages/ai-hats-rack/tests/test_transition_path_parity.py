"""HATS-1302: Parity tests for Kernel.transition and Kernel.transition_ops."""

from __future__ import annotations

import pytest
from rack_testkit import make_kernel

from ai_hats_rack.cardschema import StateGateError
from ai_hats_rack.ops import parse_ops


def test_transition_and_transition_ops_refuse_gates_identically(tmp_path):
    kernel = make_kernel(tmp_path)
    tid = kernel.create(actor="t", caller_cwd=tmp_path, title="Probe").task.id

    with pytest.raises(StateGateError):
        kernel.transition(tid, "cancelled", actor="t", caller_cwd=tmp_path)

    with pytest.raises(StateGateError):
        kernel.transition_ops(
            tid, parse_ops(["--state", "cancelled"]), actor="t", caller_cwd=tmp_path
        )

    card = kernel.get(tid)
    assert card.state == "brainstorm"


def test_transition_accepts_edge_name(tmp_path):
    kernel = make_kernel(tmp_path, edge_names={("done", "execute"): "reopen"})
    tid = kernel.create(actor="t", caller_cwd=tmp_path, title="Probe").task.id

    # Walk card to done
    kernel.transition(tid, "plan", actor="t", caller_cwd=tmp_path)
    kernel.transition(tid, "execute", actor="t", caller_cwd=tmp_path)
    kernel.transition(tid, "document", actor="t", caller_cwd=tmp_path)
    kernel.transition(tid, "review", actor="t", caller_cwd=tmp_path)
    kernel.transition(tid, "done", actor="t", caller_cwd=tmp_path)

    assert kernel.get(tid).state == "done"

    # Edge name "reopen" maps to done -> execute transition
    kernel.transition(tid, "reopen", actor="t", caller_cwd=tmp_path)
    assert kernel.get(tid).state == "execute"
