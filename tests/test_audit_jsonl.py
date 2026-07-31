"""Tests for AuditWriter JSONL-based audit generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats_observe import AuditWriter, Session
from ai_hats_observe.artifacts import METRICS_JSON, TRANSCRIPT_JSONL


FIXTURES = Path(__file__).parent / "fixtures"


def make_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_20260327-181454-1"
    session_dir.mkdir()
    s = Session(session_id="20260327-181454-1", session_dir=session_dir)
    s.init_audit(role="assistant", provider="claude")
    (session_dir / METRICS_JSON).write_text(
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
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("привет, как дела?"),
            assistant_msg([{"type": "text", "text": "Привет! Всё хорошо."}]),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "👤 привет, как дела?" in audit
    assert "👾 Привет! Всё хорошо." in audit


def test_jsonl_extracts_tool_calls(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("найди файл"),
            assistant_msg(
                [
                    {"type": "tool_use", "id": "t1", "name": "Grep", "input": {"pattern": "TODO"}},
                ]
            ),
            tool_result_msg("t1", "src/main.py:42: TODO fix this"),
            assistant_msg([{"type": "text", "text": "Нашёл TODO в main.py."}]),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "🔧 Grep" in audit
    assert "👾 Нашёл TODO в main.py." in audit


def test_jsonl_extracts_thinking(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("сложный вопрос"),
            assistant_msg(
                [
                    {"type": "thinking", "thinking": "Давайте подумаем об этом..."},
                    {"type": "text", "text": "Вот ответ."},
                ]
            ),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "💭" in audit
    assert "👾 Вот ответ." in audit


def test_jsonl_token_stats(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("привет"),
            assistant_msg(
                [{"type": "text", "text": "Привет!"}],
                usage={"input_tokens": 500, "output_tokens": 200},
            ),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "500" in audit
    assert "200" in audit
    assert "Model Usage" in audit


def test_jsonl_multiple_turns(tmp_path):
    session = make_session(tmp_path)
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("вопрос 1", ts="2026-03-27T18:15:00Z"),
            assistant_msg([{"type": "text", "text": "ответ 1"}], ts="2026-03-27T18:15:05Z"),
            user_msg("вопрос 2", ts="2026-03-27T18:16:00Z"),
            assistant_msg([{"type": "text", "text": "ответ 2"}], ts="2026-03-27T18:16:05Z"),
        ],
    )

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
        "18:15:00.000 [SYS] Session started\n18:15:10.000 [REQ] test request\n"
    )

    AuditWriter().build(session, jsonl_path=None)
    audit = session.audit_path.read_text()

    assert "Session Audit" in audit


# --- trace cleanup ---


def test_jsonl_path_deletes_trace(tmp_path):
    """When JSONL is available, trace.log must be deleted after build."""
    session = make_session(tmp_path)
    session.trace_path.write_text("18:15:00.000 [SYS] dummy trace\n")
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("привет"),
            assistant_msg([{"type": "text", "text": "Привет!"}]),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl)

    assert not session.trace_path.exists()
    assert session.audit_path.exists()


def test_a_long_reply_reaches_audit_md_whole(tmp_path):
    """The canonical record is lossless; size is bounded at delivery (HATS-1397).

    Measured over 184 sessions: the 500-char cut hit 97% of replies, leaving
    ~3% of the agent's own prose in the one artifact ``session-reviewer`` reads.
    ``turn.user_input`` was already exempt (HATS-683) — the reply was an oversight.
    """
    session = make_session(tmp_path)
    reply = "ы" * 4000
    jsonl = make_jsonl(tmp_path, [user_msg("go"), assistant_msg([{"type": "text", "text": reply}])])

    AuditWriter().build(session, jsonl_path=jsonl)

    audit = session.audit_path.read_text()
    assert reply in audit, "the canonical record was truncated"
    assert "…" not in audit


def test_the_trace_is_traded_for_a_copy_that_lives_here(tmp_path):
    """Deletion is licensed by a copy in the session dir, not by a file elsewhere.

    HATS-1397: ``_may_drop_trace`` assumed the provider's transcript outlives us.
    Claude expires its JSONL after ~30–40 days — measured on this corpus, only
    178 of 374 sessions with a known id still had one — so the trace was traded
    for a copy that then vanished, leaving audit.md as the sole record.
    """
    session = make_session(tmp_path)
    session.trace_path.write_text("18:15:00.000 [SYS] dummy trace\n")
    jsonl = make_jsonl(
        tmp_path,
        [user_msg("привет"), assistant_msg([{"type": "text", "text": "Привет!"}])],
    )

    AuditWriter().build(session, jsonl_path=jsonl)

    copy = session.session_dir / TRANSCRIPT_JSONL
    assert copy.read_bytes() == jsonl.read_bytes(), "the source must survive next to the session"
    assert not session.trace_path.exists()


def test_an_unwritable_session_dir_keeps_the_trace(tmp_path):
    """No copy, no delete: the trade must not become a loss."""
    session = make_session(tmp_path)
    session.trace_path.write_text("18:15:00.000 [SYS] dummy trace\n")
    jsonl = make_jsonl(
        tmp_path,
        [user_msg("привет"), assistant_msg([{"type": "text", "text": "Привет!"}])],
    )
    # A directory where the copy wants to be: the write fails, nothing else does.
    (session.session_dir / TRANSCRIPT_JSONL).mkdir()

    AuditWriter().build(session, jsonl_path=jsonl)

    assert session.trace_path.exists(), "the only copy of the session text was deleted"


def test_fallback_path_keeps_trace(tmp_path):
    """No JSONL ⇒ trace.log is the only copy of the session text — keep it.

    HATS-1374 inverts the pre-existing contract (this test asserted the delete).
    On the trace-only surfaces the scrape is lossy: agy yields zero turns because
    the patterns are Claude's, so the audit came out a header stub and the source
    went with it. 295 sessions ended up with no session text at all — 134 of them
    agy. Deletion is now allowed only when a structured transcript both exists on
    disk and parsed, because that copy outlives us.
    """
    session = make_session(tmp_path)
    session.trace_path.write_text(
        "18:15:00.000 [SYS] Session started\n18:15:10.000 [REQ] test request\n"
    )

    AuditWriter().build(session, jsonl_path=None)

    assert session.trace_path.exists(), "the only copy of the session text was deleted"
    assert session.audit_path.exists()


def test_unparseable_jsonl_keeps_trace(tmp_path):
    """A structured transcript that yields no turns does not license the delete.

    Guards the other half: `jsonl_path` existing is not proof the text survived
    — an empty or unreadable transcript leaves the trace as the only record.
    """
    session = make_session(tmp_path)
    session.trace_path.write_text("18:15:00.000 [SYS] Session started\n")
    empty = make_jsonl(tmp_path, [])

    AuditWriter().build(session, jsonl_path=empty)

    assert session.trace_path.exists()


def test_keep_raw_preserves_trace_jsonl(tmp_path):
    """keep_raw=True preserves trace.log even with JSONL."""
    session = make_session(tmp_path)
    session.trace_path.write_text("18:15:00.000 [SYS] dummy trace\n")
    jsonl = make_jsonl(
        tmp_path,
        [
            user_msg("привет"),
            assistant_msg([{"type": "text", "text": "Привет!"}]),
        ],
    )

    AuditWriter().build(session, jsonl_path=jsonl, keep_raw=True)

    assert session.trace_path.exists()
    assert session.audit_path.exists()


def test_keep_raw_preserves_trace_fallback(tmp_path):
    """keep_raw=True preserves trace.log in fallback path."""
    session = make_session(tmp_path)
    session.trace_path.write_text(
        "18:15:00.000 [SYS] Session started\n18:15:10.000 [REQ] test request\n"
    )

    AuditWriter().build(session, jsonl_path=None, keep_raw=True)

    assert session.trace_path.exists()
    assert session.audit_path.exists()


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
