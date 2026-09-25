"""The holder's stdout pump and its sources, below the process boundary (ADR-0038 D4)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from ai_hats_observe.canonical import AgentId, ResponseEnded, TurnEnded, WorthRecording
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

                def close(self):
                    return real.close()

            return Once()

    relay = _Relay(
        _child({"type": "boom"}, {"type": "result", "is_error": False, "subtype": "success"}),
        Choking(),
        event_log=log,
        report=said.append,
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
        _child(_assistant("req_1"), b"not json"), ClaudeWire(), event_log=log, report=print
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
