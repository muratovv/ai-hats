"""KimiParser tests against sanitized spike fixtures (real wire.jsonl records)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from ai_hats.surfaces.kimi.parser import KimiParser


def _fixture(name: str) -> Path:
    return Path(str(files("ai_hats.surfaces.kimi.tests").joinpath(f"fixtures/{name}")))


def test_parse_toolcall_session():
    parsed = KimiParser().parse(_fixture("wire-toolcall.jsonl"), Path("/nonexistent.trace"))
    assert len(parsed.turns) == 1
    turn = parsed.turns[0]
    assert turn.user_input and "spike-tool-test" in turn.user_input
    assert turn.tools == ["Bash"]
    assert turn.response == "done"
    assert turn.thinking  # think parts kept verbatim
    assert turn.timestamp  # epoch-ms converted
    # usage.record rows aggregate per model, tokens counted once
    assert parsed.model_stats["kimi-code/kimi-for-coding"]["calls"] == 2
    assert parsed.model_stats["kimi-code/kimi-for-coding"]["out"] == 96
    assert parsed.agg_usage["output_tokens"] == 96
    assert parsed.agg_usage["cache_read_input_tokens"] == 39936
    assert "no-token-telemetry" not in parsed.flags
    assert "schema-drift" not in parsed.flags


def test_parse_simple_session():
    parsed = KimiParser().parse(_fixture("wire-simple.jsonl"), Path("/nonexistent.trace"))
    assert len(parsed.turns) == 1
    assert parsed.turns[0].response == "ok"
    assert parsed.agg_usage["output_tokens"] > 0


def test_parse_full_session_with_additive_event_types():
    """A real full session emits ~10 event types beyond the spike's minimum —
    additive types must not read as drift (the gate-run record, sanitized)."""
    parsed = KimiParser().parse(_fixture("wire-full-session.jsonl"), Path("/nonexistent.trace"))
    assert len(parsed.turns) == 1
    assert parsed.turns[0].response.strip() == "ok"
    assert parsed.agg_usage["output_tokens"] > 0
    assert "schema-drift" not in parsed.flags


def test_unknown_event_type_is_additive_noise_not_drift(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text(
        '{"type":"metadata","protocol_version":1}\n{"type":"brand.new.event","time":1}\n'
    )
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" not in parsed.flags


def test_parsed_event_shape_violation_flags_drift(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text(
        '{"type":"metadata","protocol_version":1}\n'
        '{"type":"usage.record","model":"m","usage":"oops","time":1}\n'
    )
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
    assert any(s.reason == "unsupported_record" for s in parsed.signals)


def test_non_object_part_flags_drift_and_drops_no_turn(tmp_path: Path):
    """A scalar `part` in content.part must flag drift, not raise — and the
    turn already collected must survive (drift is a signal, never an exception)."""
    wire = tmp_path / "wire.jsonl"
    wire.write_text(
        '{"type":"metadata","protocol_version":1}\n'
        '{"type":"turn.prompt","input":[{"type":"text","text":"hi"}],"time":1}\n'
        '{"type":"context.append_loop_event","event":{"type":"content.part","part":"oops"},"time":2}\n'
        '{"type":"context.append_loop_event","event":{"type":"content.part",'
        '"part":{"type":"text","text":"ok"}},"time":3}\n'
    )
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
    assert any(s.reason == "unsupported_record" for s in parsed.signals)
    assert parsed.turns[0].response == "ok"


def test_non_numeric_usage_value_flags_drift_without_partial_counts(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text(
        '{"type":"metadata","protocol_version":1}\n'
        '{"type":"usage.record","model":"m","usage":{"inputOther":10,"output":"lots"},"time":1}\n'
        '{"type":"usage.record","model":"m","usage":{"inputOther":10,"output":5},"time":2}\n'
    )
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
    # The malformed row is dropped whole — no partial in/out counted.
    assert parsed.model_stats["m"] == {"in": 10, "out": 5, "calls": 1}
    assert parsed.agg_usage["output_tokens"] == 5


def test_non_object_record_flags_drift(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text('{"type":"metadata","protocol_version":1}\n[1,2,3]\n"a string"\n')
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags


def test_unknown_protocol_version_flags_drift(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text('{"type":"metadata","protocol_version":99}\n')
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags


def test_missing_record_falls_back_to_trace(tmp_path: Path):
    parsed = KimiParser().parse(tmp_path / "nope.jsonl", tmp_path / "t.trace")
    assert parsed.turns == []
    assert "no-token-telemetry" not in parsed.flags  # absence is not drift


def test_unparsable_line_is_drift_not_crash(tmp_path: Path):
    wire = tmp_path / "wire.jsonl"
    wire.write_text('{"type":"metadata","protocol_version":1}\nnot-json\n')
    parsed = KimiParser().parse(wire, tmp_path / "t.trace")
    assert "schema-drift" in parsed.flags
