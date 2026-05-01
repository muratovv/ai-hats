"""Tests for retro.reminder — stale-retro nudge logic."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ai_hats.models import ReminderConfig, SessionRetroConfig
from ai_hats.retro import reminder


def _make_session(project: Path, sid: str, *, role: str = "assistant",
                  turns: int = 6, tool_calls: int = 12) -> None:
    """Create a session under .gitlog/ with metrics.json."""
    sd = project / ".gitlog" / f"session_{sid}"
    sd.mkdir(parents=True)
    (sd / "metrics.json").write_text(json.dumps({
        "turns": turns, "tool_calls": tool_calls, "role": role, "exit_code": 0,
    }))


def _sid_with_date(d: date, suffix: str = "abc") -> str:
    """find_candidates parses session_id[:8] as YYYYMMDD."""
    return f"{d.strftime('%Y%m%d')}_{suffix}"


def _config(*, enabled: bool = True, max_skipped: int = 5,
            window_days: int = 14) -> SessionRetroConfig:
    return SessionRetroConfig(
        reminder=ReminderConfig(
            enabled=enabled, max_skipped=max_skipped, window_days=window_days,
        ),
    )


class TestDisabled:
    def test_returns_none_with_log_reason(self, tmp_path):
        info, why = reminder.evaluate(tmp_path, _config(enabled=False))
        assert info is None
        assert why == "disabled"


class TestEmptyProject:
    def test_no_gitlog_at_all(self, tmp_path):
        info, why = reminder.evaluate(tmp_path, _config())
        assert info is None
        assert "under threshold" in why


class TestUnderThreshold:
    def test_some_skipped_but_below_max(self, tmp_path):
        # 3 sessions in window, threshold = 5 → no fire.
        today = date.today()
        for i in range(3):
            _make_session(tmp_path, _sid_with_date(today - timedelta(days=i), f"s{i}"))
        info, why = reminder.evaluate(tmp_path, _config(max_skipped=5))
        assert info is None
        assert "3<5" in why


class TestThresholdFires:
    def test_at_or_above_threshold(self, tmp_path):
        today = date.today()
        for i in range(5):
            _make_session(tmp_path, _sid_with_date(today - timedelta(days=i), f"s{i}"))
        info, why = reminder.evaluate(tmp_path, _config(max_skipped=5, window_days=14))
        assert info is not None
        assert info["count"] == 5
        assert info["window_days"] == 14
        assert info["since"]  # ISO date string set
        assert info["command"].startswith("ai-hats reflect --since ")
        assert info["command"].endswith(" --interactive")
        assert "fired" in why
        assert "5>=5" in why

    def test_window_excludes_old_sessions(self, tmp_path):
        # 5 sessions but all older than the 7-day window.
        today = date.today()
        for i in range(5):
            _make_session(
                tmp_path, _sid_with_date(today - timedelta(days=30 + i), f"old{i}"),
            )
        info, why = reminder.evaluate(tmp_path, _config(max_skipped=5, window_days=7))
        assert info is None
        assert "0<5" in why


class TestExistingRetroSkipsCount:
    def test_session_with_retro_does_not_count(self, tmp_path):
        today = date.today()
        for i in range(5):
            sid = _sid_with_date(today - timedelta(days=i), f"s{i}")
            _make_session(tmp_path, sid)
        # Mark 2 as already having retros — should drop count to 3 → no fire.
        retro_dir = tmp_path / ".agent" / "retrospectives" / "sessions" / "llm"
        retro_dir.mkdir(parents=True)
        for i in range(2):
            sid = _sid_with_date(today - timedelta(days=i), f"s{i}")
            (retro_dir / f"{sid}.md").write_text("# retro")
        info, why = reminder.evaluate(tmp_path, _config(max_skipped=5))
        assert info is None
        assert "3<5" in why


class TestExcludedRoles:
    def test_judge_sessions_do_not_count(self, tmp_path):
        today = date.today()
        for i in range(10):
            _make_session(
                tmp_path, _sid_with_date(today - timedelta(days=i), f"j{i}"),
                role="judge",
            )
        info, why = reminder.evaluate(tmp_path, _config(max_skipped=5))
        assert info is None
        assert "0<5" in why
