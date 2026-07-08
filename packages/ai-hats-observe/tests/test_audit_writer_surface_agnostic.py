"""HATS-948 (T15) — AuditWriter is surface-agnostic (holds zero provider parsing).

Two guards: (1) a fake ``TranscriptParser`` fully drives ``build`` — the writer
never reaches for a Claude parser; (2) ``audit.py``'s source carries none of the
moved provider parsing (⏺ chrome, spinner table, JSONL field walk, regex).
RED-under-revert: re-inlining any parser into ``AuditWriter`` fails one or both.
"""

from __future__ import annotations

from pathlib import Path

import ai_hats_observe.audit as audit_module
from ai_hats_observe.audit import AuditWriter
from ai_hats_observe.parsers.base import ParsedTranscript, Turn
from ai_hats_observe.session import Session

# Markers that live ONLY in a concrete parser — never in a surface-agnostic writer.
_PROVIDER_PARSE_MARKERS = (
    "⏺",              # Claude-TUI response chrome (trace fallback)
    "_SPINNER_CHARS",  # trace-chrome noise table
    "re.compile",      # any provider regex
    "_parse_jsonl",    # Claude JSONL structured walk
    "TraceEntry",      # trace-parse internal type
    "_extract_turns",  # trace-parse turn reconstruction
)


def test_audit_source_holds_no_provider_parsing() -> None:
    src = Path(audit_module.__file__).read_text()
    present = [m for m in _PROVIDER_PARSE_MARKERS if m in src]
    assert not present, f"AuditWriter re-grew provider parsing: {present}"


class _FakeParser:
    """A non-Claude parser: fixed turns, records that it was consulted."""

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, jsonl_path, trace_path) -> ParsedTranscript:
        self.calls += 1
        return ParsedTranscript(
            turns=[
                Turn(
                    timestamp="2026-01-01T09:00:00",
                    user_input="ask",
                    response="reply",
                    tools=["Bash: ls"],
                )
            ]
        )


def test_fake_parser_fully_drives_build(tmp_path: Path) -> None:
    session_dir = tmp_path / "session_20260101-090000-1"
    session_dir.mkdir()
    session = Session(session_id="20260101-090000-1", session_dir=session_dir)
    session.init_audit(role="assistant", provider="acme")

    fake = _FakeParser()
    AuditWriter(parser=fake).build(session, jsonl_path=Path("ignored.jsonl"))

    assert fake.calls == 1
    audit = session.audit_path.read_text()
    assert "👤 ask" in audit
    assert "👾 reply" in audit
    assert "🔧 Bash: ls" in audit
