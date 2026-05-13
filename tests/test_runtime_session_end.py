"""Tests for _format_tokens (HATS-057) and _finalize_session (HATS-086)."""

from __future__ import annotations

import calendar
import json
import os
from pathlib import Path

import pytest

from ai_hats import runtime as runtime_module
from ai_hats.models import LifecycleEvent
from ai_hats.observe import Session
from ai_hats.paths import runs_dir
from ai_hats.runtime import (
    _discover_claude_jsonl,
    _finalize_session,
    _format_tokens,
)


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


# ---------------------------------------------------------------------------
# _finalize_session — guaranteed cleanup + session-end print (HATS-086)
# ---------------------------------------------------------------------------


class _StubHooksRunner:
    """Minimal HooksRunner stub. If `exc` is set, .run() raises it."""

    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc
        self.calls: list = []

    def run(self, event, env=None):
        self.calls.append(event)
        if self.exc is not None:
            raise self.exc
        return []


class _StubTracer:
    """Minimal SidecarTracer stub. flush_response() is a no-op unless `exc` set."""

    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc
        self.flushed = False

    def flush_response(self) -> None:
        self.flushed = True
        if self.exc is not None:
            raise self.exc


@pytest.fixture
def finalize_kwargs(tmp_path):
    """Build keyword args for `_finalize_session(...)` with sensible defaults.

    Test-specific behavior is injected by overriding `hooks_runner` or
    monkeypatching `runtime_module.AuditWriter`.
    """
    session = make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")
    return {
        "session": session,
        "exit_code": 0,
        "active_role": "primary",
        "provider_name": "claude",
        "claude_session_id": "abc-123",
        "project_dir": tmp_path,
        "env": {},
        "hooks_runner": _StubHooksRunner(),
        "tracer": _StubTracer(),
    }


def test_finalize_session_prints_summary_on_clean_run(finalize_kwargs, capsys):
    """Happy path: every cleanup step succeeds, summary box prints."""
    _finalize_session(**finalize_kwargs)

    out = capsys.readouterr().out
    assert "Session test complete!" in out
    # The session-end hook ran (proves we got past tracer flush).
    assert finalize_kwargs["hooks_runner"].calls == [LifecycleEvent.SESSION_END]
    assert finalize_kwargs["tracer"].flushed


def test_finalize_session_prints_summary_when_hooks_runner_fails(finalize_kwargs, capsys):
    """A SESSION_END hook raising RuntimeError must NOT skip the summary."""
    finalize_kwargs["hooks_runner"] = _StubHooksRunner(exc=RuntimeError("hook boom"))

    _finalize_session(**finalize_kwargs)

    out = capsys.readouterr().out
    assert "Session test complete!" in out


def test_finalize_session_prints_summary_when_audit_writer_fails(
    finalize_kwargs, capsys, monkeypatch
):
    """AuditWriter.build raising must NOT skip the summary."""

    class _ExplodingAuditWriter:
        def build(self, *args, **kwargs):
            raise RuntimeError("audit boom")

    monkeypatch.setattr(runtime_module, "AuditWriter", _ExplodingAuditWriter)

    _finalize_session(**finalize_kwargs)

    out = capsys.readouterr().out
    assert "Session test complete!" in out


def test_finalize_session_prints_summary_on_keyboard_interrupt_in_step(finalize_kwargs, capsys):
    """A SECOND Ctrl+C raised during a cleanup step (here: from the hook
    runner) must still let _finalize_session run to completion and print
    the summary. KeyboardInterrupt MUST NOT propagate out of finalize."""
    finalize_kwargs["hooks_runner"] = _StubHooksRunner(exc=KeyboardInterrupt())

    # Must not raise.
    _finalize_session(**finalize_kwargs)

    out = capsys.readouterr().out
    assert "Session test complete!" in out


def test_finalize_session_prints_summary_when_finalize_audit_fails(
    finalize_kwargs, capsys, monkeypatch
):
    """Session.finalize_audit raising must NOT skip the summary."""

    def _explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(finalize_kwargs["session"], "finalize_audit", _explode)

    _finalize_session(**finalize_kwargs)

    out = capsys.readouterr().out
    assert "Session test complete!" in out


# ---------------------------------------------------------------------------
# HATS-158 — retro line in session-end banner + persistent retro.log
# ---------------------------------------------------------------------------


def test_print_session_end_without_retro(tmp_path, capsys):
    """Legacy call (retro=None) → no retro line in banner."""
    from ai_hats.runtime import _print_session_end

    session = make_session(tmp_path)
    _print_session_end(session, trace_stats={"trace_size": 0, "req_count": 0}, retro=None)

    out = capsys.readouterr().out
    assert "Session test complete!" in out
    assert "📝 Retro" not in out


@pytest.mark.parametrize("action,expected_fragment", [
    ("run", "generating"),
    ("skip", "skipped"),
    ("hint", "hint — ai-hats session retro"),
])
def test_print_session_end_with_retro(tmp_path, capsys, action, expected_fragment):
    """Each action yields a dedicated retro line with the right phrasing."""
    from ai_hats.runtime import _print_session_end

    session = make_session(tmp_path)
    decision = {
        "action": action,
        "reason": "threshold met (turns=9, tool_calls=155)" if action != "skip"
        else "below threshold (turns=0<1, tool_calls=0<1)",
        "mode": "llm",
        "background": True,
        "retro_path": str(tmp_path / "retros" / "llm" / "test.md"),
        "log_path": str(runs_dir(tmp_path) / "session_test" / "retro.log"),
    }
    _print_session_end(session, trace_stats={"trace_size": 0, "req_count": 0}, retro=decision)

    out = capsys.readouterr().out
    assert "📝 Retro:" in out
    assert expected_fragment in out


def test_finalize_session_writes_runtime_decision_line(finalize_kwargs, tmp_path, monkeypatch):
    """_finalize_session must write a 'runtime decision' line to retro.log
    BEFORE the hook fires — so even a hook crash leaves a trace."""
    # Write a minimal ai-hats.yaml so should_run reads it.
    import yaml
    (tmp_path / "ai-hats.yaml").write_text(yaml.dump({
        "schema_version": 2,
        "provider": "claude",
        "active_role": "primary",
        "feedback": {"session_retro": {"policy": "smart",
                     "smart_threshold": {"min_turns": 1, "min_tool_calls": 1},
                     "mode": "programmatic", "background": True}},
    }))
    # Force the hook to blow up after runtime writes its decision line.
    finalize_kwargs["hooks_runner"] = _StubHooksRunner(exc=RuntimeError("hook boom"))

    _finalize_session(**finalize_kwargs)

    log = runs_dir(tmp_path) / "session_test" / "retro.log"
    assert log.exists(), "retro.log must be created by runtime before hooks fire"
    content = log.read_text()
    assert "runtime\tdecision" in content
    # Short session (no metrics → turns=0) → skip with threshold reason.
    assert "skip" in content


# ---------------------------------------------------------------------------
# HATS-272 — JSONL discovery fallback for resume mode
# ---------------------------------------------------------------------------


def _claude_dir_for(home: Path, project_dir: Path) -> Path:
    project_key = str(project_dir).replace("/", "-")
    d = home / ".claude" / "projects" / project_key
    d.mkdir(parents=True)
    return d


def _set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def test_discover_claude_jsonl_picks_most_recent_after_session_start(
    tmp_path, monkeypatch,
):
    """In resume mode our generated UUID never reaches Claude, so the
    JSONL ends up under Claude's own uuid. The discoverer must locate
    it via mtime ≥ session start."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    session_id = "20260507-154102-1"
    # Stale JSONL pre-dating session — must be ignored.
    stale = claude_dir / "stale.jsonl"
    stale.write_text("{}")
    _set_mtime(stale, calendar.timegm((2026, 5, 7, 14, 0, 0, 0, 0, 0)))

    # Active JSONL written during session — must win.
    active = claude_dir / "active.jsonl"
    active.write_text("{}")
    _set_mtime(active, calendar.timegm((2026, 5, 7, 16, 0, 0, 0, 0, 0)))

    found = _discover_claude_jsonl(project_dir, session_id)
    assert found is not None
    assert found.name == "active.jsonl"


def test_discover_claude_jsonl_returns_none_when_no_match(tmp_path, monkeypatch):
    """All JSONLs predate the session → fallback fails gracefully."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    old = claude_dir / "old.jsonl"
    old.write_text("{}")
    _set_mtime(old, calendar.timegm((2026, 5, 7, 10, 0, 0, 0, 0, 0)))

    found = _discover_claude_jsonl(project_dir, "20260507-154102-1")
    assert found is None


def test_discover_claude_jsonl_returns_none_when_dir_missing(tmp_path, monkeypatch):
    """No Claude project dir at all → None, not crash."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    project_dir = tmp_path / "proj"
    project_dir.mkdir()

    found = _discover_claude_jsonl(project_dir, "20260507-154102-1")
    assert found is None


def test_finalize_session_uses_discovered_jsonl_when_configured_path_missing(
    finalize_kwargs, tmp_path, monkeypatch,
):
    """Resume mode regression: configured ``--session-id`` path doesn't exist
    on disk (because Claude used its own uuid). The finalize fallback must
    discover the real JSONL and pass it to ``AuditWriter.build`` so token
    metrics get populated instead of staying zero."""
    # Use a real session_id with a parseable timestamp prefix so the
    # discoverer's datetime parsing succeeds.
    session = finalize_kwargs["session"]
    session.session_id = "20260507-154102-1"

    # Fake ~/.claude project dir with one JSONL whose mtime sits inside
    # the session lifetime — discoverer should pick it up.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    project_dir = finalize_kwargs["project_dir"]
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    real_jsonl = claude_dir / "real-claude-uuid.jsonl"
    real_jsonl.write_text("{}")
    _set_mtime(real_jsonl, calendar.timegm((2026, 5, 7, 16, 0, 0, 0, 0, 0)))

    captured: dict = {}

    class _CapturingAuditWriter:
        def build(self, session, jsonl_path=None, keep_raw=False):
            captured["jsonl_path"] = jsonl_path

    monkeypatch.setattr(runtime_module, "AuditWriter", _CapturingAuditWriter)

    # claude_session_id = our generated uuid that NEVER reached Claude.
    finalize_kwargs["claude_session_id"] = "dead-uuid-never-passed-to-claude"

    _finalize_session(**finalize_kwargs)

    assert captured["jsonl_path"] == real_jsonl, (
        "Expected discovered JSONL to be passed to AuditWriter "
        "(HATS-272 — resume-mode fallback)."
    )
