"""``gate_log`` — what the dispatcher appends to the session's ``events.jsonl``
for one verdict, read back through the canonical decoder."""

from __future__ import annotations

from pathlib import Path

from ai_hats.session_identity import SessionIdentity
from ai_hats.surfaces.gate_log import record_verdict
from ai_hats.surfaces.hook_channel import ChainDecision, ChainVerdict, HookCall, HookEvent
from ai_hats_observe.canonical import AskKind, GateDecision, GateVerdict, PersonAsked
from ai_hats_observe.event_log import read_events


def _env(tmp_path: Path) -> dict[str, str]:
    return SessionIdentity(
        id="sid-1",
        role="assistant",
        provider="claude",
        project_dir=tmp_path,
        session_dir=tmp_path / "runs" / "sid-1",
    ).to_env()


def _call(tool: str, call_id: str) -> HookCall:
    return HookCall(payload={"tool_name": tool, "tool_use_id": call_id, "tool_input": {}})


def test_an_ask_verdict_also_opens_a_wait_on_the_person(tmp_path: Path) -> None:
    """``ask`` hands the call to a person: the verdict says a gate asked, the
    wait says the run is now on the person — the one state a controller cannot
    infer from the stream. Same call id, so the tool's result closes both."""
    verdict = ChainVerdict(
        decision=ChainDecision.ASK,
        reason="git push is shared state",
        hook="ai-hats:safety-guard",
        event=HookEvent.PRE_TOOL_USE,
    )

    path = record_verdict(verdict, HookEvent.PRE_TOOL_USE, [_call("Bash", "c1")], _env(tmp_path))

    assert path is not None
    events = list(read_events(path))
    assert [type(e) for e in events] == [GateVerdict, PersonAsked]
    asked = events[1]
    assert (asked.kind, asked.call_id, asked.tool, asked.source) == (
        AskKind.PERMISSION,
        "c1",
        "Bash",
        "chain",
    )
    assert asked.detail == "git push is shared state"
    assert asked.ts is not None and asked.ts >= events[0].ts


def test_an_allow_or_deny_opens_no_wait(tmp_path: Path) -> None:
    """Positive control: only ``ask`` puts the run on a person."""
    env = _env(tmp_path)
    for decision in (ChainDecision.ALLOW, ChainDecision.DENY):
        verdict = ChainVerdict(decision=decision, event=HookEvent.PRE_TOOL_USE)
        path = record_verdict(verdict, HookEvent.PRE_TOOL_USE, [_call("Bash", "c2")], env)
        assert path is not None

    events = list(read_events(path))
    assert [type(e) for e in events] == [GateVerdict, GateVerdict]
    assert [e.decision for e in events] == [GateDecision.ALLOW, GateDecision.DENY]
