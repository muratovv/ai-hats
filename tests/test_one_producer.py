"""The one-fact-one-event check the e2e suite runs over every journal it leaves."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HELPER = Path(__file__).parent / "e2e" / "_helpers" / "one_producer.py"


def _load():
    spec = importlib.util.spec_from_file_location("one_producer", _HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["one_producer"] = module
    spec.loader.exec_module(module)
    return module


duplicate_facts = _load().duplicate_facts


def ev(name: str, /, **fields: object) -> dict[str, object]:
    return {"v": "events/v1", "event": name, **fields}


def test_a_response_replayed_under_another_agent_is_one_fact_twice() -> None:
    journal = [
        ev("response_started", response_id="r1"),
        ev("tool_result_received", call_id="c1", ok=True),
        ev("response_started", response_id="r1", agent="a-fork"),
        ev("tool_result_received", call_id="c1", ok=True, agent="a-fork"),
    ]

    found = duplicate_facts(journal)

    assert any("response_started" in f and "r1" in f for f in found)
    assert any("tool_result_received" in f and "c1" in f for f in found)


def test_two_gates_judging_one_call_are_two_facts() -> None:
    """The chain allowed, then the person refused: two gates, not two producers."""
    journal = [
        ev("gate_verdict", point="before_tool", decision="allow", hook="", call_id="c1"),
        ev("gate_verdict", point="before_tool", decision="deny", hook="person", call_id="c1"),
        ev("person_asked", call_id="c1"),
        ev("prompt_received", prompt_id="p1"),
        ev("prompt_received", prompt_id=None),
        ev("prompt_received", prompt_id=None),
    ]

    assert duplicate_facts(journal) == []


def test_one_gate_judging_one_call_twice_is_reported() -> None:
    journal = [
        ev("gate_verdict", point="before_tool", decision="deny", hook="person", call_id="c1"),
        ev("gate_verdict", point="before_tool", decision="deny", hook="person", call_id="c1"),
        ev("person_asked", call_id="c2"),
        ev("person_asked", call_id="c2"),
    ]

    found = duplicate_facts(journal)

    assert len(found) == 2


def wait() -> dict[str, object]:
    return ev("signal", obligation="harness_must_act", kind="wait")


def test_the_run_turns_and_quota_walls_are_each_said_once() -> None:
    journal = [
        ev("run_started"),
        ev("prompt_received", prompt_id="p1"),
        wait(),
        ev("turn_ended", prompt_ids=["p1"]),
        ev("prompt_received", prompt_id="p2"),
        wait(),
        wait(),
        ev("turn_ended", prompt_ids=["p1", "p2"]),
        ev("run_ended"),
        ev("gate_verdict", point="stop", hook="x"),
        ev("run_started"),
    ]

    found = duplicate_facts(journal)

    assert any("run_started" in f for f in found)
    assert any("run_ended" in f and "last" in f for f in found)
    assert any("turn_ended" in f and "p1" in f for f in found)
    assert sum("wait" in f for f in found) == 1


def test_one_wall_per_turn_and_a_closed_run_is_clean() -> None:
    journal = [
        ev("run_started"),
        ev("prompt_received", prompt_id="p1"),
        wait(),
        ev("turn_ended", prompt_ids=["p1"]),
        wait(),
        ev("turn_ended", prompt_ids=[]),
        ev("prompt_received", prompt_id=None),
        wait(),
        ev("response_started", response_id="r-sub", agent="a1"),
        ev("signal", obligation="harness_must_act", kind="wait", agent="a1"),
        ev("signal", obligation="harness_must_act", kind="wait", agent="a1"),
        ev("run_ended"),
    ]

    assert duplicate_facts(journal) == []
