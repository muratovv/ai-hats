"""Tests for AuditWriter JSONL-based audit generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.observe import AuditWriter, Session


FIXTURES = Path(__file__).parent / "fixtures"


def make_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_20260327-181454-1"
    session_dir.mkdir()
    s = Session(session_id="20260327-181454-1", session_dir=session_dir)
    s.init_audit(role="assistant", provider="claude")
    (session_dir / "metrics.json").write_text(
        json.dumps({"role": "assistant", "provider": "claude", "exit_code": 0})
    )
    return s


def make_jsonl(tmp_path, messages: list[dict]) -> Path:
    """Create a minimal JSONL file from message dicts."""
    path = tmp_path / "conversation.jsonl"
    with open(path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return path


def user_msg(text: str, ts: str = "2026-03-27T18:15:00Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def assistant_msg(
    content: list[dict],
    ts: str = "2026-03-27T18:15:05Z",
    usage: dict | None = None,
) -> dict:
    msg = {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": content,
            "usage": usage or {"input_tokens": 100, "output_tokens": 50},
        },
    }
    return msg


def tool_result_msg(tool_use_id: str, result: str, ts: str = "2026-03-27T18:15:03Z") -> dict:
    return {
        "type": "user",
        "timestamp": ts,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result}],
        },
    }


# --- unit tests ---


def test_jsonl_extracts_user_message(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(tmp_path, [
        user_msg("привет, как дела?"),
        assistant_msg([{"type": "text", "text": "Привет! Всё хорошо."}]),
    ])

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "👤 привет, как дела?" in audit
    assert "👾 Привет! Всё хорошо." in audit


def test_jsonl_extracts_tool_calls(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(tmp_path, [
        user_msg("найди файл"),
        assistant_msg([
            {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "TODO"}},
        ]),
        tool_result_msg("t1", "src/main.py:42: TODO fix this"),
        assistant_msg([{"type": "text", "text": "Нашёл TODO в main.py."}]),
    ])

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "🔧 Grep" in audit
    assert "👾 Нашёл TODO в main.py." in audit


def test_jsonl_extracts_thinking(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(tmp_path, [
        user_msg("сложный вопрос"),
        assistant_msg([
            {"type": "thinking", "thinking": "Давайте подумаем об этом..."},
            {"type": "text", "text": "Вот ответ."},
        ]),
    ])

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "💭" in audit
    assert "👾 Вот ответ." in audit


def test_jsonl_token_stats(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(tmp_path, [
        user_msg("привет"),
        assistant_msg(
            [{"type": "text", "text": "Привет!"}],
            usage={"input_tokens": 500, "output_tokens": 200},
        ),
    ])

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "500" in audit
    assert "200" in audit
    assert "Model Usage" in audit


def test_jsonl_multiple_turns(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(tmp_path, [
        user_msg("вопрос 1", ts="2026-03-27T18:15:00Z"),
        assistant_msg([{"type": "text", "text": "ответ 1"}], ts="2026-03-27T18:15:05Z"),
        user_msg("вопрос 2", ts="2026-03-27T18:16:00Z"),
        assistant_msg([{"type": "text", "text": "ответ 2"}], ts="2026-03-27T18:16:05Z"),
    ])

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "## Turn 1" in audit
    assert "## Turn 2" in audit
    assert "вопрос 1" in audit
    assert "вопрос 2" in audit
    assert "ответ 1" in audit
    assert "ответ 2" in audit


def test_fallback_to_trace_when_no_jsonl(tmp_path):
    """When jsonl_path is None, falls back to trace.log parsing."""
    session = make_session(tmp_path)
    session.trace_path.write_text(
        '18:15:00.000 [SYS] Session started\n'
        '18:15:10.000 [REQ] test request\n'
    )

    AuditWriter().build(session, jsonl_path=None)
    audit = session.audit_path.read_text()

    assert "Session Audit" in audit


# --- integration test ---


@pytest.mark.integration
def test_build_from_real_jsonl(tmp_path):
    """Real Claude JSONL → clean enriched audit.md."""
    real_jsonl = FIXTURES / "real_conversation.jsonl"
    if not real_jsonl.exists():
        pytest.skip("No real JSONL fixture")

    session = make_session(tmp_path)
    AuditWriter().build(session, jsonl_path=real_jsonl)
    audit = session.audit_path.read_text()

    assert "## Turn" in audit
    assert "👤" in audit
    assert "👾" in audit
    # Must NOT have PTY noise
    assert "Pondering" not in audit
    assert "✳" not in audit
    assert len(audit) < 6000
