"""``ClaudeTranscriptReader.feed`` — claude's stream-json stdout read as the main agent's record.

Shapes come from a live claude 2.1.281 session (``fixtures/wire/``: one session
seen from both sides, the wire and its transcript) and from lines built to the
same shape where a test needs one kind alone.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from ai_hats_observe.canonical import (
    GateVerdict,
    HarnessActionRequired,
    HarnessMustAct,
    Notice,
    PersonActionRequired,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    ToolResultReceived,
    TurnEnded,
    WorthRecording,
)
from ai_hats_observe.canonical.types import Completion, GateDecision, GatePoint, PromptOrigin
from ai_hats_observe.event_log import encode
from ai_hats_observe.parsers.claude_events import SOURCE, WIRE_SOURCE, ClaudeTranscriptReader


def _assistant(request: str, text: str, *, output_tokens: int = 3) -> dict[str, Any]:
    """One fragment as the wire sends it: ``stop_reason`` null, usage mid-stream."""
    return {
        "type": "assistant",
        "uuid": f"u-{request}-{len(text)}",
        "timestamp": "2026-09-24T12:00:00.000Z",
        "request_id": request,
        "parent_tool_use_id": None,
        "message": {
            "id": f"msg_{request}",
            "model": "claude-x",
            "stop_reason": None,
            "usage": {"input_tokens": 10, "output_tokens": output_tokens},
            "content": [{"type": "text", "text": text}],
        },
    }


def _delta(stop_reason: str, output_tokens: int) -> dict[str, Any]:
    return {
        "type": "stream_event",
        "parent_tool_use_id": None,
        "event": {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason},
            "usage": {
                "input_tokens": 10,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 7,
                "cache_creation_input_tokens": 5,
            },
        },
    }


def _result(**fields: Any) -> dict[str, Any]:
    return {"type": "result", "subtype": "success", "is_error": False, **fields}


def _feed(*lines: dict[str, Any]) -> list:
    reader = ClaudeTranscriptReader(None)
    return [event for line in lines for event in reader.feed(line)]


def test_message_delta_ends_the_response_with_its_final_usage_before_the_turn() -> None:
    events = _feed(
        _assistant("req_1", "PAPAYA"),
        _delta("end_turn", 234),
        _result(terminal_reason="completed"),
    )

    kinds = [type(e) for e in events]
    assert kinds.index(ResponseStarted) < kinds.index(ResponseEnded) < kinds.index(TurnEnded)
    ended = next(e for e in events if isinstance(e, ResponseEnded))
    assert ended.response_id == "req_1", "the wire's request_id is the record's requestId"
    assert (ended.usage.output_tokens, ended.usage.cache_read_input_tokens) == (234, 7)
    assert (ended.stop_reason, ended.completion) == ("end_turn", Completion.COMPLETE)
    turn = events[-1]
    assert isinstance(turn, TurnEnded) and (turn.ok, turn.raw_code) == (True, "completed")
    assert turn.ts, "the moment the result arrived: the wire's result carries no time"


def test_a_response_the_wire_never_closed_ends_before_its_turn() -> None:
    """No ``message_delta`` (a cut stream, a future binary): ``result`` still
    closes the response first, so the turn's events never trail its end."""
    events = _feed(_assistant("req_1", "half"), _result(terminal_reason="completed"))

    assert [type(e) for e in events][-2:] == [ResponseEnded, TurnEnded]
    assert events[-2].completion == Completion.UNKNOWN


def test_a_sub_agents_line_is_left_to_its_own_record() -> None:
    line = {**_assistant("req_sub", "inside the child"), "parent_tool_use_id": "toolu_parent"}

    assert _feed(line) == []


def test_the_echo_of_a_prompt_is_the_harness_speaking() -> None:
    echo = {
        "type": "user",
        "isReplay": True,
        "uuid": "3f0e2d9c-0000-4000-8000-000000000001",
        "timestamp": "2026-09-24T12:00:00.000Z",
        "parent_tool_use_id": None,
        "message": {"role": "user", "content": "read a.txt"},
    }

    (prompt,) = _feed(echo)

    assert isinstance(prompt, PromptReceived)
    assert (prompt.text, prompt.origin) == ("read a.txt", PromptOrigin.HARNESS)


def test_an_api_error_line_is_a_signal_and_no_response() -> None:
    line = {
        "type": "assistant",
        "error": "rate_limit",
        "is_api_error_message": True,
        "request_id": "req_e",
        "parent_tool_use_id": None,
        "message": {
            "id": "e",
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "429"}],
        },
    }

    events = _feed(line, _result(is_error=True, terminal_reason="api_error", result="429"))

    assert isinstance(events[0], HarnessActionRequired)
    assert events[0].reason == HarnessMustAct.WAIT and events[0].source == WIRE_SOURCE
    assert not any(isinstance(e, (ResponseStarted, ResponseEnded)) for e in events)
    assert (events[-1].ok, events[-1].detail) == (False, "429")


def _quota(status: str, resets_at: int = 1790340000) -> dict[str, Any]:
    """Shaped as measured on 2.1.282: utilization lives per window, not on top."""
    return {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": status,
            "resetsAt": resets_at,
            "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.91, "resetsAt": resets_at}},
        },
    }


def test_a_warning_from_the_quota_is_said_before_the_wall() -> None:
    """Only the wire says the quota is close: the record never does, and a PTY
    session hears it from its status line instead."""
    (notice,) = _feed(_quota("allowed_warning"))

    assert (notice.reason, notice.raw_code) == (WorthRecording.APPROACHING_LIMIT, "allowed_warning")
    assert notice.source == WIRE_SOURCE and notice.ts
    assert "five_hour" in notice.detail and "91%" in notice.detail


def test_the_wall_is_the_api_error_and_takes_its_reset_from_the_quota_line() -> None:
    """One wall, one producer: the refused turn's API error, in both inputs. Its
    reset rides the message in the record and the quota line on the wire."""
    wall = {
        "type": "assistant",
        "error": "rate_limit",
        "is_api_error_message": True,
        "request_id": "req_e",
        "parent_tool_use_id": None,
        "message": {
            "id": "e",
            "model": "<synthetic>",
            "content": [{"type": "text", "text": "429"}],
        },
    }

    events = _feed(_quota("rejected"), wall, _result(is_error=True, result="429"))
    waits = [e for e in events if isinstance(e, HarnessActionRequired)]

    assert [(w.reason, w.retry_after) for w in waits] == [(HarnessMustAct.WAIT, 1790340000)]
    # POSITIVE CONTROL: without the quota line the wall still stands, with no reset
    (alone,) = [e for e in _feed(wall) if isinstance(e, HarnessActionRequired)]
    assert alone.retry_after is None


@pytest.mark.parametrize(
    "line",
    [
        {"type": "command_lifecycle", "command_uuid": "x", "state": "queued"},
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}},
        {"type": "system", "subtype": "init", "model": "claude-x"},
        {"type": "system", "subtype": "status", "status": "requesting"},
        {"type": "system", "subtype": "hook_started", "hook_event": "Stop"},
        {"type": "system", "subtype": "notification", "key": "stop-hook-error"},
        {"type": "stream_event", "event": {"type": "content_block_delta", "index": 0}},
        {"type": "stream_event", "event": {"type": "message_start", "message": {}}},
    ],
)
def test_the_wires_bookkeeping_says_nothing(line: dict[str, Any]) -> None:
    assert _feed({**line, "parent_tool_use_id": None}) == []


def test_an_unknown_line_is_drift_said_by_the_wire() -> None:
    (notice,) = _feed({"type": "keep_alive"})

    assert isinstance(notice, Notice)
    assert (notice.reason, notice.raw_code) == (WorthRecording.UNSUPPORTED_RECORD, "keep_alive")
    assert notice.source == WIRE_SOURCE and notice.ts, "stamped when it arrived"


def _hook(event: str, exit_code: int, *, stdout: str = "", stderr: str = "") -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "hook_response",
        "hook_id": "h",
        "hook_name": event if event == "Stop" else f"{event}:Read",
        "hook_event": event,
        "exit_code": exit_code,
        "outcome": "success" if exit_code == 0 else "error",
        "stdout": stdout,
        "stderr": stderr,
    }


def test_each_stop_hook_is_a_verdict_at_the_stop() -> None:
    (verdict,) = _feed(_hook("Stop", 0))

    assert isinstance(verdict, GateVerdict)
    assert (verdict.point, verdict.decision) == (GatePoint.AT_STOP, GateDecision.ALLOW)
    assert verdict.source == WIRE_SOURCE


def test_a_stop_hook_that_fails_or_blocks_also_warns_as_the_record_does() -> None:
    """``exit 2`` keeps the model going; the record reads it as ``allow`` plus
    its text in ``hookErrors``, and the wire says the same."""
    verdict, warning = _feed(_hook("Stop", 2, stderr="say BYE too\n"))

    assert verdict.decision == GateDecision.ALLOW
    assert isinstance(warning, Notice) and warning.reason == WorthRecording.SURFACE_WARNING
    assert "say BYE too" in (warning.detail or "")


def test_a_stop_hook_that_ends_the_run_is_a_deny() -> None:
    (verdict,) = _feed(_hook("Stop", 0, stdout='{"continue": false, "stopReason": "budget"}'))

    assert (verdict.decision, verdict.reason) == (GateDecision.DENY, "budget")


def test_a_failed_hook_elsewhere_is_a_warning_and_a_quiet_one_is_nothing() -> None:
    (warning,) = _feed(_hook("PostToolUse", 1, stderr="hookboom\n"))

    assert warning.reason == WorthRecording.SURFACE_WARNING
    assert warning.detail == "PostToolUse:Read exit 1: hookboom"
    assert _feed(_hook("PostToolUse", 0)) == []
    assert _feed(_hook("PreToolUse", 2, stderr="blocked")) == [], "exit 2 is unmeasured: silent"


# --- one session, both sides: the wire and its transcript ------------------

WIRE = Path(__file__).parent / "fixtures" / "wire"


def _wire_events(name: str) -> list:
    reader = ClaudeTranscriptReader(None)
    lines = (WIRE / f"{name}.wire.jsonl").read_text(encoding="utf-8").splitlines()
    events = [e for line in lines for e in reader.feed(json.loads(line))]
    reader.close()
    return events + list(reader.read())


def _record_events(name: str) -> list:
    return list(ClaudeTranscriptReader(WIRE / f"{name}.transcript.jsonl").read())


def _comparable(events: list) -> Counter:
    """The events with the declared differences taken out (ADR-0037, the reader
    with two inputs): when a thing arrived, who said it, how a hook failure and
    an API error are worded, the turn's end, the Stop verdicts' count, and the
    record kinds the wire never carries."""
    kept = []
    for event in events:
        if isinstance(event, TurnEnded):
            continue
        if isinstance(event, GateVerdict) and event.point == GatePoint.AT_STOP:
            continue
        record = encode(event)
        record.pop("ts", None)
        if record.get("source") == WIRE_SOURCE:
            record["source"] = SOURCE
        if record.get("kind") == WorthRecording.SURFACE_WARNING.value:
            record["raw_code"] = record["detail"] = "<hook failure>"
        if isinstance(event, (HarnessActionRequired, PersonActionRequired)):
            record["raw_code"] = "<api error code>"
        kept.append(json.dumps(record, sort_keys=True))
    return Counter(kept)


@pytest.mark.parametrize("name", ["p1", "p2"])
def test_the_wire_and_the_record_of_one_session_say_the_same(name: str) -> None:
    wire, record = _comparable(_wire_events(name)), _comparable(_record_events(name))

    assert wire == record, {"wire only": wire - record, "record only": record - wire}


def test_the_comparison_catches_a_usage_the_wire_got_wrong() -> None:
    """Negative control: the same wire with one ``message_delta`` count changed
    no longer matches its record — the equality above is not vacuous."""
    lines = [json.loads(x) for x in (WIRE / "p1.wire.jsonl").read_text().splitlines()]
    delta = next(x for x in lines if x.get("event", {}).get("type") == "message_delta")
    delta["event"]["usage"]["output_tokens"] += 1
    reader = ClaudeTranscriptReader(None)
    tampered = [e for line in lines for e in reader.feed(line)]

    assert _comparable(tampered) != _comparable(_record_events("p1"))


def test_the_wire_gives_a_stop_verdict_per_hook_and_the_record_one_per_stop() -> None:
    """The declared difference, measured: two Stop hooks at each of three stops."""

    def stops(events: list) -> list:
        return [
            e.decision
            for e in events
            if isinstance(e, GateVerdict) and e.point == GatePoint.AT_STOP
        ]

    assert stops(_wire_events("p1")) == [GateDecision.ALLOW] * 6
    assert stops(_record_events("p1")) == [GateDecision.ALLOW] * 3


# --- the prompt's id, from its receipt to its turn's end -------------------


def test_the_echo_carries_the_prompts_id_and_the_result_the_ids_it_answered() -> None:
    u1, u2 = "3f0e2d9c-0000-4000-8000-000000000001", "3f0e2d9c-0000-4000-8000-000000000002"
    echo = {
        "type": "user",
        "isReplay": True,
        "uuid": u1,
        "parent_tool_use_id": None,
        "message": {"role": "user", "content": "one"},
    }

    prompt, ended = _feed(echo, _result(user_message_uuids=[u1, u2]))

    assert prompt.prompt_id == u1
    assert ended.prompt_ids == (u1, u2), "a folded prompt ends with the one it joined"


def test_a_turn_the_surface_started_answers_no_prompt() -> None:
    (ended,) = _feed(_result(user_message_uuids=[]))

    assert ended.prompt_ids == ()


def test_a_record_prompt_has_its_records_uuid_as_its_id() -> None:
    """Parity of the modes: in a PTY session the prompt's id is its record's uuid."""
    record = {
        "type": "user",
        "uuid": "3f0e2d9c-0000-4000-8000-000000000003",
        "promptSource": "sdk",
        "message": {"role": "user", "content": "typed"},
    }
    reader = ClaudeTranscriptReader(None)

    (prompt,) = list(reader._record(record))

    assert prompt.prompt_id == "3f0e2d9c-0000-4000-8000-000000000003"


def test_a_tool_the_mode_refused_is_its_error_result_said_once() -> None:
    """Measured on 2.1.282 under ``dontAsk``: ``system/permission_denied`` and
    the result's ``permission_denials`` repeat the ``is_error`` result — the one
    thing the record has, so the one producer in both modes (ADR-0038 D4)."""
    text = (
        "Permission to use Bash has been denied because Claude Code is running in don't ask mode."
    )
    call = {"type": "tool_use", "id": "toolu_d", "name": "Bash", "input": {"command": "touch x"}}
    events = _feed(
        {
            **_assistant("req_d", "x"),
            "message": {**_assistant("req_d", "x")["message"], "content": [call]},
        },
        {
            "type": "system",
            "subtype": "permission_denied",
            "tool_name": "Bash",
            "tool_use_id": "toolu_d",
            "decision_reason_type": "mode",
            "message": text,
        },
        {
            "type": "user",
            "parent_tool_use_id": None,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_d",
                        "content": text,
                        "is_error": True,
                    }
                ],
            },
        },
        _result(
            terminal_reason="completed",
            permission_denials=[
                {
                    "tool_name": "Bash",
                    "tool_use_id": "toolu_d",
                    "tool_input": {"command": "touch x"},
                }
            ],
        ),
    )

    assert not [e for e in events if isinstance(e, Notice)], "no drift, no second producer"
    (refused,) = [e for e in events if isinstance(e, ToolResultReceived)]
    assert (refused.call_id, refused.ok) == ("toolu_d", False)
    assert isinstance(events[-1], TurnEnded)
