"""Regression tests for ``AuditWriter._parse_jsonl`` (HATS-529).

Locks the parser's contract against drift in the ``claude`` binary's
JSONL session format. Post-HATS-535, ``_parse_jsonl`` is the sole
source of ``audit.md`` content (Path A — the live PTY ⏺-marker
accumulator — was removed; trace.log fallback handles only the
JSONL-missing degraded mode). A silent schema drift here would break
``audit.md`` for every consumer.

Fixture: ``tests/fixtures/claude_jsonl/three_turns_with_tool.jsonl``
— scrubbed per the whitelist documented in the sibling ``README.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.observe import AuditWriter, Session

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "claude_jsonl"
    / "three_turns_with_tool.jsonl"
)


@pytest.fixture
def parsed():
    """Parse the fixture once per test that needs it."""
    return AuditWriter()._parse_jsonl(FIXTURE)


# ---------------------------------------------------------------- _parse_jsonl


def test_turn_count_is_two_exit_filtered(parsed):
    """``/exit`` user message is filtered (starts with ``/``) → 2 turns, not 3.

    Documents the slash-command exception to ``count(👾) == count(👤)``.
    """
    turns, _, _ = parsed
    assert len(turns) == 2


def test_text_only_turn_extracts_user_and_response(parsed):
    turns, _, _ = parsed
    t1 = turns[0]
    assert t1.user_input == "alpha"
    assert t1.response == "alpha-response"
    assert t1.tools == []
    # No thinking block in turn 1 → thinking_secs stays 0.
    assert t1.thinking_secs == 0


def test_tool_use_turn_collapses_assistant_blocks_into_single_turn(parsed):
    """Turn 2: ``user beta`` → ``assistant {thinking, tool_use}`` →
    ``user tool_result`` (filtered, must NOT create a new Turn) →
    ``assistant text``. All three assistant messages collapse into the
    same Turn.
    """
    turns, _, _ = parsed
    t2 = turns[1]
    assert t2.user_input == "beta"
    assert t2.response == "beta-done"
    assert t2.tools == ["Bash: echo hi"]
    # Thinking block present → thinking_secs >= 1.
    assert t2.thinking_secs >= 1


def test_tool_result_user_message_does_not_create_new_turn(parsed):
    """Regression guard: ``_extract_user_text`` returns ``None`` for any
    content list containing a ``tool_result`` block. Without this, the
    fixture would yield 3 turns and Turn 2 would be split.
    """
    turns, _, _ = parsed
    # Fixture has 4 user messages (alpha, beta, tool_result, /exit) but
    # only 2 produce turns (tool_result + /exit filtered).
    assert len(turns) == 2


def test_model_stats_aggregate_three_assistant_calls(parsed):
    """3 assistant messages in fixture → 3 calls on the same model;
    token sums must match the stubbed ``usage`` numbers (50+60+70=180 in,
    5+30+8=43 out).
    """
    _, model_stats, _ = parsed
    assert set(model_stats.keys()) == {"claude-opus-4-7"}
    stats = model_stats["claude-opus-4-7"]
    assert stats["calls"] == 3
    assert stats["in"] == 180
    assert stats["out"] == 43


def test_agg_usage_includes_cache_reads(parsed):
    """Cache read tokens from turns 2 and 3 (100 + 160) aggregate."""
    _, _, agg = parsed
    assert agg["input_tokens"] == 180
    assert agg["output_tokens"] == 43
    assert agg["cache_read_input_tokens"] == 260
    assert agg["cache_creation_input_tokens"] == 0


def test_turn_order_preserved(parsed):
    """Parser must preserve fixture-order; alpha before beta."""
    turns, _, _ = parsed
    assert [t.user_input for t in turns] == ["alpha", "beta"]


# ---------------------------------------------------------------- _format_audit


def test_rendered_audit_contains_user_bot_and_tool_markers(tmp_path):
    """End-to-end through ``_format_audit``: the produced ``audit.md``
    must carry 👤 / 👾 / 🔧 markers in the right turns.
    """
    session = Session(session_id="t", session_dir=tmp_path)
    session.metrics_path.write_text('{"role": "test", "provider": "claude"}')

    writer = AuditWriter()
    turns, model_stats, _ = writer._parse_jsonl(FIXTURE)
    rendered = writer._format_audit(session, turns, model_stats=model_stats)

    # Turn 1 — text-only.
    assert "👤 alpha" in rendered
    assert "👾 alpha-response" in rendered
    # Turn 2 — tool_use + text.
    assert "👤 beta" in rendered
    assert "👾 beta-done" in rendered
    assert "🔧 Bash: echo hi" in rendered
    # No third turn block.
    assert "## Turn 3" not in rendered
    # Header reflects the per-model totals — pin the exact "Tokens:
    # 180 in / 43 out" formatting so a future refactor that changes
    # the separator (e.g. comma → slash) fails this guard, not
    # downstream consumers.
    assert "180 in / 43 out" in rendered
