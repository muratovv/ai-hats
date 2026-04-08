"""Tests for _format_tokens — token usage line in session summary (HATS-057)."""

from __future__ import annotations

import json

from ai_hats.observe import Session
from ai_hats.runtime import _format_tokens


def make_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def test_format_tokens_happy_path(tmp_path):
    """Full tokens block → formatted line with thousand separators."""
    session = make_session(tmp_path)
    session.metrics_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "input": 12345,
                    "output": 6789,
                    "cache_read": 45678,
                    "cache_creation": 1234,
                },
            }
        )
    )

    line = _format_tokens(session)

    assert line == "🪙 📥 12,345 in   📤 6,789 out   •   ♻️  45,678 hit   ✨ 1,234 new"


def test_format_tokens_zero_cache(tmp_path):
    """Cache fields default to 0 when missing."""
    session = make_session(tmp_path)
    session.metrics_path.write_text(
        json.dumps(
            {
                "tokens": {"input": 100, "output": 50},
            }
        )
    )

    line = _format_tokens(session)

    assert line == "🪙 📥 100 in   📤 50 out   •   ♻️  0 hit   ✨ 0 new"


def test_format_tokens_missing_metrics_file(tmp_path):
    """No metrics.json → fallback line."""
    session = make_session(tmp_path)
    assert not session.metrics_path.exists()

    assert _format_tokens(session) == "🪙 Tokens: n/a"


def test_format_tokens_missing_tokens_block(tmp_path):
    """metrics.json exists but no 'tokens' key (gemini provider) → fallback."""
    session = make_session(tmp_path)
    session.metrics_path.write_text(
        json.dumps(
            {
                "exit_code": 0,
                "role": "primary",
                "provider": "gemini",
            }
        )
    )

    assert _format_tokens(session) == "🪙 Tokens: n/a"


def test_format_tokens_corrupt_json(tmp_path):
    """Invalid JSON → fallback, no exception raised."""
    session = make_session(tmp_path)
    session.metrics_path.write_text("{not valid json")

    assert _format_tokens(session) == "🪙 Tokens: n/a"


def test_format_tokens_empty_tokens_dict(tmp_path):
    """Empty tokens block → treated as missing (no zeros line)."""
    session = make_session(tmp_path)
    session.metrics_path.write_text(json.dumps({"tokens": {}}))

    # Empty dict is falsy → fallback
    assert _format_tokens(session) == "🪙 Tokens: n/a"
