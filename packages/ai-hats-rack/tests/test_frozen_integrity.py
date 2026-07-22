"""Frozen-pin integrity guard (HATS-1031 Р13): drift blocks every transition,
the reason carries digests + the transition-op recovery recipe, and the
--ack-frozen hatches reopen the road."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from ai_hats_rack.cli import main
from ai_hats_rack.dispatch import Phase
from ai_hats_rack.docstore import compute_digest
from ai_hats_rack.extensions import FrozenIntegrityExtension, standalone_extensions
from ai_hats_rack.fsm import load_topology
from ai_hats_rack.kernel import Kernel


@pytest.fixture
def runner():
    return CliRunner()


def _args(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def _frozen_task(runner, tmp_path, body: bytes = b"v1") -> tuple:
    """One card with a frozen evidence.log; returns (card_dir, pinned_digest)."""
    created = runner.invoke(main, ["create", "guarded", *_args(tmp_path), "--json"])
    assert created.exit_code == 0, created.output
    card_dir = tmp_path / "tasks" / "HATS-001"
    (card_dir / "evidence.log").write_bytes(body)
    frozen = runner.invoke(
        main, ["transition", "HATS-001", "--freeze", "evidence.log", *_args(tmp_path), "--json"]
    )
    assert frozen.exit_code == 0, frozen.output
    (op,) = json.loads(frozen.output)["ops"]
    return card_dir, op["digest"]


# ----- drift blocks, with an actionable reason ------------------------------------


def test_modified_pin_blocks_any_transition_with_digests_and_recipe(runner, tmp_path):
    card_dir, pinned = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").write_bytes(b"v2-tampered")
    current = compute_digest(card_dir / "evidence.log")
    before = (card_dir / "task.yaml").read_bytes()

    result = runner.invoke(main, ["transition", "HATS-001", "plan", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    error = json.loads(result.output)["error"]
    assert error["code"] == "aborted" and error["subscriber"] == "frozen-integrity"
    reason = error["reason"]
    assert "evidence.log" in reason
    assert pinned in reason and current in reason
    assert "rack transition HATS-001 --freeze evidence.log --ack-frozen" in reason
    assert "rack transition HATS-001 --rm evidence.log --ack-frozen" in reason
    # in-lock abort: zero bytes persisted (state stays brainstorm)
    assert (card_dir / "task.yaml").read_bytes() == before


def test_missing_pinned_file_blocks_with_recipe(runner, tmp_path):
    card_dir, pinned = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").unlink()

    result = runner.invoke(main, ["transition", "HATS-001", "plan", *_args(tmp_path), "--json"])
    assert result.exit_code == 1
    reason = json.loads(result.output)["error"]["reason"]
    assert "evidence.log" in reason and pinned in reason and "missing" in reason
    assert "rack transition HATS-001 --rm evidence.log --ack-frozen" in reason


def test_force_does_not_bypass_the_guard(runner, tmp_path):
    card_dir, _ = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").write_bytes(b"v2")
    result = runner.invoke(
        main,
        ["transition", "HATS-001", "review", "--force", "--reason", "why", *_args(tmp_path), "--json"],
    )
    assert result.exit_code == 1
    assert json.loads(result.output)["error"]["subscriber"] == "frozen-integrity"


# ----- clean / absent pins pass ---------------------------------------------------


def test_clean_pin_and_no_pins_pass(runner, tmp_path):
    _frozen_task(runner, tmp_path)  # untouched frozen pin
    ok = runner.invoke(main, ["transition", "HATS-001", "plan", *_args(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output

    unpinned = runner.invoke(main, ["create", "free", *_args(tmp_path), "--json"])
    assert unpinned.exit_code == 0
    ok2 = runner.invoke(main, ["transition", "HATS-002", "plan", *_args(tmp_path), "--json"])
    assert ok2.exit_code == 0, ok2.output


# ----- recovery hatches reopen the road -------------------------------------------


def test_refreeze_hatch_then_transition_passes(runner, tmp_path):
    card_dir, _ = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").write_bytes(b"v2-accepted")
    refrozen = runner.invoke(
        main,
        ["transition", "HATS-001", "--freeze", "evidence.log", "--ack-frozen", *_args(tmp_path)],
    )
    assert refrozen.exit_code == 0, refrozen.output
    ok = runner.invoke(main, ["transition", "HATS-001", "plan", *_args(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output


def test_rm_hatch_then_transition_passes(runner, tmp_path):
    card_dir, _ = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").write_bytes(b"v2")
    removed = runner.invoke(
        main,
        ["transition", "HATS-001", "--rm", "evidence.log", "--ack-frozen", *_args(tmp_path)],
    )
    assert removed.exit_code == 0, removed.output
    ok = runner.invoke(main, ["transition", "HATS-001", "plan", *_args(tmp_path), "--json"])
    assert ok.exit_code == 0, ok.output


def test_one_composite_refreeze_and_state_passes_the_guard(runner, tmp_path):
    # Ops run in argv order under one lock: the guard sees the IN-MEMORY card,
    # so an earlier --freeze op already satisfies a later state op's scan.
    card_dir, _ = _frozen_task(runner, tmp_path)
    (card_dir / "evidence.log").write_bytes(b"v2")
    result = runner.invoke(
        main,
        [
            "transition", "HATS-001", "--freeze", "evidence.log",
            "--state", "plan", "--ack-frozen", *_args(tmp_path), "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task"]["state"] == "plan"
    assert [op["op"] for op in payload["ops"]] == ["freeze", "state"]


# ----- wiring: subscriptions + the standalone kit ---------------------------------


def test_guard_subscribes_to_the_full_edge_product(tmp_path):
    ext = FrozenIntegrityExtension(tmp_path / "tasks")
    subs = ext.subscriptions()
    keys = {s.event_key for s in subs}
    states = load_topology().states
    # every ordered pair of distinct states + the execute reclaim self-loop
    assert len(keys) == len(states) * (len(states) - 1) + 1
    assert "edge:execute--execute" in keys
    assert all(s.phase is Phase.IN_LOCK for s in subs)


def test_standalone_kit_runs_guard_before_plan_gate(tmp_path):
    tasks_dir = tmp_path / "tasks"
    kernel = Kernel(tasks_dir, subscribers=standalone_extensions(tasks_dir))
    order = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    ]
    assert order == ["frozen-integrity", "plan-consent", "plan-gate"]
