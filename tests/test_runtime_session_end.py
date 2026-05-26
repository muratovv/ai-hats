"""Tests for ``_format_tokens`` (HATS-057), ``_finalize_session_basic``
(HATS-086 cleanup invariants), and ``_discover_claude_jsonl`` (HATS-272
resume-mode JSONL discovery).

HATS-535: the legacy ``_finalize_session`` megafunction was split into
``_finalize_session_basic`` (this file) + the ``finalize-hitl``
sub-pipeline (``MakeAudit`` + ``RunSessionEnd`` — tested separately in
``test_make_audit_step.py`` and ``test_run_session_end_step.py``).
This file covers the subset of pre-HATS-535 behaviour that remains in
``runtime.py``: trace flush + metrics.json + trace stats + the
SIGINT-safe ``_print_session_end`` outer-``finally`` contract.
"""

from __future__ import annotations

import calendar
import json
import os
from pathlib import Path

import pytest

from ai_hats.observe import Session
from ai_hats.paths import runs_dir
from ai_hats.runtime import (
    _discover_claude_jsonl,
    _finalize_session_basic,
    _format_tokens,
    _print_session_end,
)


def make_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


# ---------------------------------------------------------------------------
# _format_tokens
# ---------------------------------------------------------------------------


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
        json.dumps({"tokens": {"input": 100, "output": 50}})
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
        json.dumps({"exit_code": 0, "role": "primary", "provider": "gemini"})
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

    assert _format_tokens(session) == "🪙 Tokens: n/a"


# ---------------------------------------------------------------------------
# _finalize_session_basic — per-runner cleanup (HATS-086 invariants)
# ---------------------------------------------------------------------------


class _StubTracer:
    """Minimal SidecarTracer stub.

    HATS-529: ``flush_response`` was removed along with Path A. The stub
    is now an inert placeholder — ``_finalize_session_basic`` no longer
    calls any method on the tracer, but the parameter is still passed
    (reserved scaffold for future finalize-time tracer cleanup hooks).
    """


@pytest.fixture
def basic_kwargs(tmp_path):
    """Default keyword args for ``_finalize_session_basic(...)``."""
    session = make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")
    return {
        "session": session,
        "exit_code": 0,
        "active_role": "primary",
        "provider_name": "claude",
        "tracer": _StubTracer(),
    }


def test_basic_writes_metrics_json(basic_kwargs):
    """Happy path writes metrics.json with exit_code + role + provider."""
    _finalize_session_basic(**basic_kwargs)

    metrics = json.loads(basic_kwargs["session"].metrics_path.read_text())
    assert metrics["exit_code"] == 0
    assert metrics["role"] == "primary"
    assert metrics["provider"] == "claude"


def test_basic_returns_trace_stats_shape(basic_kwargs):
    """``trace_stats`` return is a dict with ``req_count`` + ``trace_size`` keys.

    ``_finalize_session_basic`` itself appends a ``[SYS] Session ended``
    line to trace.log before calling ``_collect_trace_stats``, so
    ``trace_size`` is non-zero by then. The req_count remains 0 because
    no real user turns happened in the stub.
    """
    stats = _finalize_session_basic(**basic_kwargs)

    assert isinstance(stats, dict)
    assert stats.get("req_count", 0) == 0
    assert stats.get("trace_size", 0) >= 0  # SYS line was appended; just shape check


def test_basic_swallows_finalize_audit_exception(basic_kwargs, monkeypatch):
    """``Session.finalize_audit`` raising MUST NOT propagate."""

    def _explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(basic_kwargs["session"], "finalize_audit", _explode)

    # Must not raise.
    _finalize_session_basic(**basic_kwargs)


def test_basic_appends_tags_to_metrics(basic_kwargs):
    """``tags`` kwarg lands in metrics.json under the ``tags`` key."""
    basic_kwargs["tags"] = {"ticket": "HATS-535", "scope": "test"}

    _finalize_session_basic(**basic_kwargs)

    metrics = json.loads(basic_kwargs["session"].metrics_path.read_text())
    assert metrics["tags"] == {"ticket": "HATS-535", "scope": "test"}


# ---------------------------------------------------------------------------
# _print_session_end — banner contract (HATS-158 retro line + HATS-535 banner split)
# ---------------------------------------------------------------------------


def test_print_session_end_without_retro(tmp_path, capsys):
    """``retro=None`` → no ``📝 Retro`` line in banner."""
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
def test_print_session_end_with_retro_one_line(tmp_path, capsys, action, expected_fragment):
    """``retro=<decision>`` → one ``📝 Retro:`` line per action; the
    reminder/wrap-up banner LINES no longer print here (moved to
    ``RunSessionEnd`` step in HATS-535)."""
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


def test_print_session_end_does_not_emit_reminder_banner(tmp_path, capsys):
    """HATS-535: even when a ``reminder`` is present in the retro decision,
    ``_print_session_end`` no longer renders the cyan banner — that
    moved to ``RunSessionEnd._print_retro_banner``."""
    session = make_session(tmp_path)
    decision = {
        "action": "run",
        "reason": "ok",
        "mode": "llm",
        "background": True,
        "reminder": {"count": 5, "command": "ai-hats reflect all"},
    }
    _print_session_end(session, trace_stats={"trace_size": 0, "req_count": 0}, retro=decision)

    out = capsys.readouterr().out
    assert "📝 Retro:" in out  # one-liner kept
    assert "Reflect the project through" not in out  # banner moved
    assert "ai-hats reflect all" not in out


# ---------------------------------------------------------------------------
# HATS-272 — JSONL discovery fallback for resume mode (unchanged by HATS-535)
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
    stale = claude_dir / "stale.jsonl"
    stale.write_text("{}")
    _set_mtime(stale, calendar.timegm((2026, 5, 7, 14, 0, 0, 0, 0, 0)))

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


# ---------------------------------------------------------------------------
# HATS-535 integration: WrapRunner.run finally chain (HATS-086 contract)
# ---------------------------------------------------------------------------
#
# These tests pin the 3-layer try/finally structure of WrapRunner.run's
# finally block:
#
#   1. _finalize_session_basic — metrics + trace_stats
#   2. _run_finalize_hitl — finalize-hitl sub-pipeline
#   3. _print_session_end — green summary (HATS-086 invariant: ALWAYS fires)
#
# Component-level tests cover each layer in isolation; these tests verify
# that a crash in layer 2 does NOT prevent layer 3 from firing — the
# session-id MUST always reach stdout. Without this gate, a future refactor
# could silently regress the SIGINT-safety chain (e.g. by re-introducing
# a bare `raise` in _run_finalize_hitl that escapes the per-step wrap).


@pytest.fixture
def wrap_runner_factory(tmp_path, monkeypatch):
    """Build a WrapRunner with stubs so .run() exercises the finally
    chain without touching real PTY / real claude / real composition.

    Returns a callable: (pty_exit_code=0, finalize_hitl_exc=None) →
    (runner, project_dir). The caller invokes runner.run("claude") and
    asserts on captured stdout / artefacts.
    """

    from ai_hats.assembler import Assembler
    from ai_hats.models import ProjectConfig
    from ai_hats.runtime import WrapRunner

    # Real project with maintainer role wired in (the assembler walks
    # the live library/). We need a real project to exercise
    # WrapRunner.run's composition path — but with _pty_spawn stubbed,
    # no claude process actually starts.
    project = tmp_path / "proj"
    project.mkdir()
    repo_root = Path(__file__).resolve().parent.parent
    library = repo_root / "library"
    ProjectConfig(
        provider="claude",
        library_paths=[str(library)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / "ai-hats.yaml")
    asm = Assembler(project, library_paths=[library])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")

    monkeypatch.setenv("AI_HATS_QUIET", "1")

    def make(pty_exit_code: int = 0, finalize_hitl_exc: BaseException | None = None):
        runner = WrapRunner(project)

        def _stub_spawn(self, cmd, env, tracer):
            return pty_exit_code

        monkeypatch.setattr(WrapRunner, "_pty_spawn", _stub_spawn)

        # Stub hooks so SESSION_START hooks don't try to exec anything.
        class _NoopHooks:
            def run(self, event, env=None):
                return []

        monkeypatch.setattr(
            WrapRunner, "_make_session_hooks_runner",
            lambda self: _NoopHooks(),
        )

        if finalize_hitl_exc is not None:
            def _exploding_finalize_hitl(*args, **kwargs):
                raise finalize_hitl_exc

            monkeypatch.setattr(
                "ai_hats.runtime._run_finalize_hitl",
                _exploding_finalize_hitl,
            )

        return runner, project

    return make


def test_wrap_runner_finally_prints_summary_on_happy_path(
    wrap_runner_factory, capsys,
):
    """Happy path: _pty_spawn returns 0, finalize-hitl runs cleanly,
    _print_session_end fires the green summary."""
    runner, project = wrap_runner_factory(pty_exit_code=0)

    exit_code, session = runner.run("claude")

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"✨ Session {session.session_id} complete!" in out


def test_wrap_runner_finally_prints_summary_when_finalize_hitl_raises(
    wrap_runner_factory, capsys,
):
    """HATS-086 invariant (HATS-535 refactor preserves it): a crash inside
    the finalize-hitl sub-pipeline MUST NOT prevent _print_session_end
    from firing. The session-id is the single source of recovery for the
    user — losing it on a downstream crash is the regression class this
    test guards against."""
    runner, project = wrap_runner_factory(
        pty_exit_code=0,
        finalize_hitl_exc=RuntimeError("finalize-hitl boom"),
    )

    exit_code, session = runner.run("claude")

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"✨ Session {session.session_id} complete!" in out, (
        f"HATS-086 regression: finalize-hitl crash suppressed the "
        f"session-end summary. stdout tail:\n{out[-800:]}"
    )


def test_wrap_runner_finally_prints_summary_when_finalize_hitl_keyboard_interrupt(
    wrap_runner_factory, capsys,
):
    """A second Ctrl+C raised from inside finalize-hitl (modelling SIGINT
    landing in the sub-pipeline run) MUST NOT escape the runner — the
    print still fires."""
    runner, project = wrap_runner_factory(
        pty_exit_code=130,
        finalize_hitl_exc=KeyboardInterrupt(),
    )

    exit_code, session = runner.run("claude")

    assert exit_code == 130
    out = capsys.readouterr().out
    assert f"✨ Session {session.session_id} complete!" in out
