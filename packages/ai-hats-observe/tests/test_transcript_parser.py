"""HATS-948 (T15) — the TranscriptParser adapter + surface-agnostic AuditWriter.

``AuditWriter`` orchestrates + formats; a ``TranscriptParser`` owns all surface
parsing (Claude JSONL structured / trace-chrome fallback). RED-under-revert:
re-inlining a parser into ``AuditWriter`` (so a fake parser no longer drives it)
fails ``test_audit_writer_delegates_to_injected_parser``.
"""

from __future__ import annotations

from ai_hats_observe.audit import AuditWriter
from ai_hats_observe.parsers.base import ParsedTranscript, Turn, TranscriptParser
from ai_hats_observe.parsers.claude import ClaudeParser
from ai_hats_observe.parsers.trace import TraceParser
from ai_hats_observe.session import Session


def _session(tmp_path) -> Session:
    session_dir = tmp_path / "session_20260327-181454-1"
    session_dir.mkdir()
    s = Session(session_id="20260327-181454-1", session_dir=session_dir)
    s.init_audit(role="assistant", provider="claude")
    return s


def test_parsed_transcript_defaults() -> None:
    pt = ParsedTranscript(turns=[])
    assert pt.turns == []
    assert pt.model_stats == {}
    assert pt.agg_usage["input_tokens"] == 0


def test_concrete_parsers_satisfy_protocol() -> None:
    assert isinstance(ClaudeParser(), TranscriptParser)
    assert isinstance(TraceParser(), TranscriptParser)


def test_claude_parser_reads_jsonl(tmp_path) -> None:
    import json

    jsonl = tmp_path / "c.jsonl"
    jsonl.write_text(
        json.dumps({"type": "user", "timestamp": "2026-03-27T18:15:00Z",
                    "message": {"content": [{"type": "text", "text": "hi"}]}})
        + "\n"
        + json.dumps({"type": "assistant", "timestamp": "2026-03-27T18:15:05Z",
                      "message": {"model": "m", "content": [{"type": "text", "text": "hello"}],
                                  "usage": {"input_tokens": 10, "output_tokens": 5}}})
        + "\n"
    )
    parsed = ClaudeParser().parse(jsonl, tmp_path / "absent.trace")
    assert [t.user_input for t in parsed.turns] == ["hi"]
    assert parsed.turns[0].response == "hello"
    assert parsed.model_stats["m"]["calls"] == 1
    assert parsed.agg_usage["input_tokens"] == 10


def test_trace_parser_reads_trace_chrome(tmp_path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text(
        "18:15:00.000 [REQ] find the file\n"
        "18:15:01.000 [RES] ⏺Found it in main.py\n"
    )
    parsed = TraceParser().parse(None, trace)
    assert parsed.turns[0].user_input == "find the file"
    assert parsed.turns[0].response == "Found it in main.py"
    assert parsed.model_stats == {}


def test_claude_parser_falls_back_to_trace(tmp_path) -> None:
    trace = tmp_path / "trace.log"
    trace.write_text("18:15:00.000 [REQ] question here\n")
    parsed = ClaudeParser().parse(None, trace)
    assert parsed.turns[0].user_input == "question here"


def test_audit_writer_delegates_to_injected_parser(tmp_path) -> None:
    class FakeParser:
        def parse(self, jsonl_path, trace_path) -> ParsedTranscript:
            return ParsedTranscript(
                turns=[Turn(timestamp="12:00:00", user_input="Q", response="A")]
            )

    session = _session(tmp_path)
    AuditWriter(parser=FakeParser()).build(session, jsonl_path=None)
    audit = session.audit_path.read_text()
    assert "👤 Q" in audit
    assert "👾 A" in audit
