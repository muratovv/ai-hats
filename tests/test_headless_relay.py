"""The holder's stdout pump and its sources, below the process boundary (ADR-0038 D4)."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ai_hats_observe.canonical import (
    AgentId,
    AskKind,
    Notice,
    PersonAsked,
    ResponseEnded,
    ToolCallId,
    TurnEnded,
    WorthRecording,
)
from ai_hats_observe.event_log import append_event
from ai_hats_observe.event_log_writer import EventSource

from ai_hats.headless.runner import HeadlessRunner, _Relay
from ai_hats.runtime_common import sub_agent_sources
from ai_hats.surfaces.claude.wire import ClaudeWire


class _Log:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, events) -> int:
        self.events.extend(events)
        return len(events)


def _child(*lines: object) -> SimpleNamespace:
    raw = b"".join((x if isinstance(x, bytes) else json.dumps(x).encode()) + b"\n" for x in lines)
    return SimpleNamespace(stdout=io.BytesIO(raw))


def _assistant(request: str) -> dict:
    return {
        "type": "assistant",
        "request_id": request,
        "parent_tool_use_id": None,
        "message": {"id": request, "content": [{"type": "text", "text": "hi"}]},
    }


def test_the_main_record_is_left_out_and_the_sub_agents_kept() -> None:
    child = EventSource(Path("sub.jsonl"), agent=AgentId("a1"))
    sources = [Path("main.jsonl"), EventSource(Path("main2.jsonl")), child]

    assert sub_agent_sources(sources) == [child]


def test_a_line_the_decoder_chokes_on_is_reported_and_the_pump_goes_on() -> None:
    said: list[str] = []
    log = _Log()

    class Choking(ClaudeWire):
        def decoder(self):
            real = super().decoder()

            class Once:
                def decode(self, line):
                    if line.get("type") == "boom":
                        raise RuntimeError("bad line")
                    return real.decode(line)

                def control(self, line):
                    return real.control(line)

                def close(self):
                    return real.close()

            return Once()

    relay = _Relay(
        _child({"type": "boom"}, {"type": "result", "is_error": False, "subtype": "success"}),
        Choking(),
        event_log=log,
        report=said.append,
        log=Path("/nowhere/events.jsonl"),
    )
    relay.follow()

    notice, ended = log.events
    assert (notice.reason, notice.raw_code) == (WorthRecording.UNSUPPORTED_RECORD, "decoder-error")
    assert "RuntimeError: bad line" in (notice.detail or ""), (
        "the client sees why, not only the trace"
    )
    assert type(ended) is TurnEnded, "the turn after the bad line still ends"
    assert len(said) == 1 and "RuntimeError: bad line" in said[0]


def test_a_line_that_is_not_a_json_object_is_drift_in_the_log() -> None:
    log = _Log()
    relay = _Relay(
        _child(
            b"Warning: not json",
            [1, 2],
            {"type": "result", "is_error": False, "subtype": "success"},
        ),
        ClaudeWire(),
        event_log=log,
        report=lambda _: None,
        log=Path("/nowhere/events.jsonl"),
    )
    relay.follow()

    assert [(type(e).__name__, getattr(e, "raw_code", None)) for e in log.events] == [
        ("Notice", "malformed-json"),
        ("Notice", "non-object-line"),
        ("TurnEnded", "success"),
    ]
    assert all(e.reason is WorthRecording.UNSUPPORTED_RECORD for e in log.events[:2])


def test_a_response_cut_by_the_surface_dying_is_ended_before_the_log_closes() -> None:
    log = _Log()
    relay = _Relay(
        _child(_assistant("req_1"), b"not json"),
        ClaudeWire(),
        event_log=log,
        report=print,
        log=Path("/nowhere/events.jsonl"),
    )
    relay.follow()

    assert type(log.events[-1]) is ResponseEnded


def test_a_session_with_no_event_log_is_refused_before_the_surface_starts(capsys) -> None:
    runner = HeadlessRunner.__new__(HeadlessRunner)
    runner._signal = None
    said: list[str] = []
    spawned: list[int] = []

    code = runner._spawn_surface(
        ["claude"],
        {},
        None,
        session=SimpleNamespace(log_sys=said.append, session_dir=Path("/nowhere")),
        event_log=None,
        provider_session_id="x",
        on_spawn=spawned.append,
    )

    assert code == 1 and spawned == []
    assert said and "no event log" in said[0]
    assert "no event log" in capsys.readouterr().err


class _Stdin:
    """The child's stdin: every line the holder wrote, and whether it was closed."""

    def __init__(self) -> None:
        self.lines: list[dict] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if self.closed:
            raise ValueError("write to closed file")
        self.lines.extend(json.loads(line) for line in data.splitlines())

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _question(call: str = "toolu_1", request: str = "req-1", reason: str | None = None) -> dict:
    return {
        "type": "control_request",
        "request_id": request,
        "request": {
            "subtype": "can_use_tool",
            "tool_name": "Bash",
            "input": {"command": "git push"},
            "tool_use_id": call,
            **({"decision_reason": reason, "decision_reason_type": "hook"} if reason else {}),
        },
    }


def _relay(tmp_path: Path, *wire_lines: object) -> tuple[_Relay, _Log, _Stdin]:
    child = _child(*wire_lines)
    child.stdin = _Stdin()
    log = _Log()
    relay = _Relay(child, ClaudeWire(), event_log=log, report=print, log=tmp_path / "events.jsonl")
    return relay, log, child.stdin


def _feed(relay: _Relay, *commands: dict) -> None:
    read, write = os.pipe()
    os.write(
        write, b"".join(json.dumps({"v": "commands/v1", **c}).encode() + b"\n" for c in commands)
    )
    os.close(write)
    try:
        relay.feed(read, "")
    finally:
        os.close(read)


def _replies(stdin: _Stdin) -> list[dict]:
    return [line["response"] for line in stdin.lines if line.get("type") == "control_response"]


def _rejections(log: _Log) -> list[Notice]:
    return [
        e
        for e in log.events
        if isinstance(e, Notice) and e.reason is WorthRecording.COMMAND_REJECTED
    ]


def test_a_question_nobody_recorded_becomes_a_person_asked(tmp_path: Path) -> None:
    relay, log, _ = _relay(tmp_path, _question(reason="a gate asks"))

    relay.follow()

    [asked] = [e for e in log.events if isinstance(e, PersonAsked)]
    assert asked.kind is AskKind.PERMISSION and asked.call_id == ToolCallId("toolu_1")
    assert asked.tool == "Bash" and asked.detail == "a gate asks"
    assert asked.source == "claude/wire"


def test_a_question_the_gate_already_recorded_is_not_recorded_twice(tmp_path: Path) -> None:
    append_event(
        PersonAsked(kind=AskKind.PERMISSION, call_id=ToolCallId("toolu_1"), source="chain"),
        tmp_path / "events.jsonl",
    )
    relay, log, _ = _relay(tmp_path, _question())

    relay.follow()

    assert not [e for e in log.events if isinstance(e, PersonAsked)]


def test_an_answer_goes_to_the_binary_as_the_reply_to_its_question(tmp_path: Path) -> None:
    relay, log, stdin = _relay(tmp_path, _question())
    relay.follow()

    _feed(relay, {"cmd": "answer", "call_id": "toolu_1", "decision": "allow"})

    [reply] = _replies(stdin)
    assert reply["request_id"] == "req-1"
    assert reply["response"] == {"behavior": "allow", "updatedInput": {"command": "git push"}}
    assert not _rejections(log)


def test_only_the_first_answer_counts(tmp_path: Path) -> None:
    relay, log, stdin = _relay(tmp_path, _question())
    relay.follow()

    _feed(
        relay,
        {"cmd": "answer", "call_id": "toolu_1", "decision": "deny"},
        {"cmd": "answer", "call_id": "toolu_1", "decision": "allow"},
        {"cmd": "answer", "call_id": "toolu_9", "decision": "allow"},
    )

    assert [r["response"]["behavior"] for r in _replies(stdin)] == ["deny"]
    whys = [n.detail for n in _rejections(log)]
    assert len(whys) == 2 and all(n.raw_code == "answer" for n in _rejections(log))
    assert "already closed" in whys[0] and "no open question" in whys[1]


def test_a_question_the_binary_took_back_takes_no_answer(tmp_path: Path) -> None:
    relay, log, stdin = _relay(
        tmp_path, _question(), {"type": "control_cancel_request", "request_id": "req-1"}
    )
    relay.follow()

    _feed(relay, {"cmd": "answer", "call_id": "toolu_1", "decision": "allow"})

    assert _replies(stdin) == []
    [rejected] = _rejections(log)
    assert "already closed" in rejected.detail


def test_a_question_open_at_the_end_of_stdin_is_denied_before_the_binary_hears_eof(
    tmp_path: Path,
) -> None:
    relay, _, stdin = _relay(tmp_path, _question())
    relay.follow()

    _feed(relay)

    [reply] = _replies(stdin)
    assert reply["response"]["behavior"] == "deny"
    assert "session is ending" in reply["response"]["message"]
    assert stdin.closed


def test_an_interrupt_goes_to_the_binary_as_its_own_request(tmp_path: Path) -> None:
    relay, log, stdin = _relay(tmp_path)

    _feed(relay, {"cmd": "interrupt"})

    [request] = [line for line in stdin.lines if line.get("type") == "control_request"]
    assert request["request"] == {"subtype": "interrupt"}
    assert not _rejections(log)


def test_an_answer_to_a_question_the_gate_announced_waits_for_the_wire(tmp_path: Path) -> None:
    """The gate records its question before the binary puts it on the wire, and
    a client reading the log can answer inside that gap."""
    append_event(
        PersonAsked(kind=AskKind.PERMISSION, call_id=ToolCallId("toolu_1"), source="chain"),
        tmp_path / "events.jsonl",
    )
    relay, log, stdin = _relay(tmp_path, _question())
    read, write = os.pipe()
    os.write(write, b'{"v":"commands/v1","cmd":"answer","call_id":"toolu_1","decision":"allow"}\n')
    feeder = threading.Thread(target=relay.feed, args=(read, ""), daemon=True)
    feeder.start()
    time.sleep(0.3)  # the answer is read before the question reaches the holder

    assert _replies(stdin) == [] and not _rejections(log), "held, not refused"
    relay.follow()
    os.close(write)
    feeder.join(5)
    os.close(read)

    [reply] = _replies(stdin)
    assert reply["request_id"] == "req-1" and reply["response"]["behavior"] == "allow"


def test_an_early_answer_nobody_announced_is_still_refused(tmp_path: Path) -> None:
    relay, log, stdin = _relay(tmp_path, _question())

    _feed(relay, {"cmd": "answer", "call_id": "toolu_1", "decision": "allow"})

    [rejected] = _rejections(log)
    assert "names no open question" in rejected.detail
