"""Tests for AuditWriter — post-processing trace.log into enriched audit.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_observe import AuditWriter, Session
from ai_hats_observe.parsers.trace import TraceEntry, TraceParser


FIXTURES = Path(__file__).parent / "fixtures"


def make_session(tmp_path, trace_content: str = "") -> Session:
    session_dir = tmp_path / "session_20260327-173234-1"
    session_dir.mkdir()
    s = Session(session_id="20260327-173234-1", session_dir=session_dir)
    if trace_content:
        s.trace_path.write_text(trace_content)
    s.init_audit(role="assistant", provider="claude")
    return s


# --- parsing ---

def test_parse_trace_entry():
    line = '17:32:34.581 [SYS] Session started: role=assistant'
    entry = TraceParser._parse_line(line)
    assert entry.timestamp == "17:32:34.581"
    assert entry.tag == "SYS"
    assert entry.content == "Session started: role=assistant"


def test_parse_trace_entry_with_res():
    line = '17:32:35.253 [RES] ⏺Привет! Ответ модели.'
    entry = TraceParser._parse_line(line)
    assert entry.tag == "RES"
    assert entry.content == "⏺Привет! Ответ модели."


def test_parse_malformed_line():
    assert TraceParser._parse_line("garbage") is None
    assert TraceParser._parse_line("") is None


# --- noise filtering ---

def test_is_noise_spinner():
    assert TraceParser._is_noise("✢")
    assert TraceParser._is_noise("✳Pondering…")
    assert TraceParser._is_noise("✶Reticulating…")
    assert TraceParser._is_noise("(thinking with high effort)")


def test_is_noise_ui():
    assert TraceParser._is_noise("╭───ClaudeCodev2.1.85───╮")
    assert TraceParser._is_noise("────────────────────────")
    assert TraceParser._is_noise("? for shortcuts")
    assert TraceParser._is_noise("esc to interrupt")


def test_is_noise_meta():
    assert TraceParser._is_noise("0;⠂ Claude Code")
    assert TraceParser._is_noise("9;4;0;")


def test_is_noise_short():
    assert TraceParser._is_noise("f")
    assert TraceParser._is_noise("rg")


def test_not_noise_response():
    assert not TraceParser._is_noise("⏺Привет! Я ассистент.")
    assert not TraceParser._is_noise("⏺Searching for 1 pattern…")


# --- tool extraction ---

def test_extract_tool_search():
    assert TraceParser._extract_tool("⏺Searching for 1 pattern…") == "Search: 1 pattern"


def test_extract_tool_read():
    assert TraceParser._extract_tool("⏺ Read(src/main.py)") is not None
    assert "Read" in TraceParser._extract_tool("⏺ Read(src/main.py)")


def test_extract_tool_bash():
    assert TraceParser._extract_tool("⏺ Bash(ls -la)") is not None
    assert "Bash" in TraceParser._extract_tool("⏺ Bash(ls -la)")


def test_extract_tool_returns_none_for_response():
    assert TraceParser._extract_tool("⏺Привет! Обычный ответ.") is None


# --- thinking detection ---

def test_thinking_duration():
    entries = [
        TraceEntry("17:32:34.000", "RES", "(thinking with high effort)"),
        TraceEntry("17:32:35.000", "RES", "✳Pondering…"),
        TraceEntry("17:32:36.000", "RES", "✶Pondering…"),
        TraceEntry("17:32:38.000", "RES", "⏺Response text"),
    ]
    duration = TraceParser._thinking_duration(entries)
    assert duration == 2  # 34 to 36 (last entry is response, not thinking)


# --- integration: real trace ---

@pytest.mark.integration
def test_build_on_real_trace(tmp_path):
    """Real trace.log → enriched audit.md with turns, tools, responses."""
    real_trace = FIXTURES / "real_trace.log"
    if not real_trace.exists():
        pytest.skip("No real trace fixture")

    session = make_session(tmp_path, real_trace.read_text())
    writer = AuditWriter()
    writer.build(session)

    audit = session.audit_path.read_text()

    # Must have structure
    assert "## Turn" in audit
    # Must have model response
    assert "👾" in audit
    # Must NOT have raw spinner noise
    assert "Pondering" not in audit
    assert "✳" not in audit
    # Must be compact
    assert len(audit) < 6000
