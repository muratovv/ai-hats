"""Tests for observe.py — SidecarTracer."""

from __future__ import annotations

import os
import sys

import pytest

from ai_hats.observe import Session, SidecarTracer
from ai_hats.paths import TRANSCRIPT_TXT


def make_test_session(tmp_path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def pipe_with(data: bytes) -> int:
    """Create a pipe pre-loaded with data, return read fd (write end closed)."""
    read_fd, write_fd = os.pipe()
    os.write(write_fd, data)
    os.close(write_fd)
    return read_fd


# --- unit tests ---

def test_master_read_logs_res_and_returns_data(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"hello from CLI\n")

    result = tracer.make_master_read()(fd)

    assert result == b"hello from CLI\n"
    trace = session.trace_path.read_text()
    assert "[RES]" in trace
    assert "hello from CLI" in trace


def test_master_read_strips_ansi(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[32mgreen text\x1b[0m\n")

    tracer.make_master_read()(fd)

    trace = session.trace_path.read_text()
    assert "green text" in trace
    assert "\x1b" not in trace


def test_master_read_skips_empty_chunks(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[0m   \x1b[K\n")  # only escapes and whitespace

    tracer.make_master_read()(fd)

    assert not session.trace_path.exists()


def test_stdin_read_buffers_until_newline(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    stdin_read = tracer.make_stdin_read()

    fd1 = pipe_with(b"partial")
    stdin_read(fd1)
    assert not session.trace_path.exists()

    fd2 = pipe_with(b"\n")
    stdin_read(fd2)
    trace = session.trace_path.read_text()
    assert "[REQ]" in trace
    assert "partial" in trace


def test_stdin_read_returns_data_unchanged(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"hello\n")

    result = tracer.make_stdin_read()(fd)

    assert result == b"hello\n"


def test_stdin_read_skips_empty_input(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\n")  # newline only — nothing to log

    tracer.make_stdin_read()(fd)

    assert not session.trace_path.exists()


def test_stdin_read_strips_zellij_prefix(tmp_path):
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b">|Zellij(4301)>|Zellij(4301)ffhello\n")

    tracer.make_stdin_read()(fd)

    trace = session.trace_path.read_text()
    assert "[REQ]" in trace
    assert "hello" in trace
    assert "Zellij" not in trace


# NOTE: HATS-529 — the live PTY ⏺-marker accumulator ("Path A") was
# removed. The 5 tests that exercised ``_res_buf`` / ``flush_response``
# semantics here have no replacement; ``AuditWriter._parse_jsonl`` is
# now the canonical audit source and is regression-guarded by
# ``tests/test_audit_writer_parse_jsonl_fixture.py``.


# --- integration test ---

@pytest.mark.integration
def test_wrap_runner_pty_spawn_writes_trace(tmp_path):
    """WrapRunner._pty_spawn uses SidecarTracer and writes [RES] to trace."""
    from ai_hats.runtime import WrapRunner

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="t", session_dir=session_dir)
    tracer = SidecarTracer(session)

    runner = object.__new__(WrapRunner)
    exit_code = runner._pty_spawn(["echo", "hello wrap"], {}, tracer)

    assert exit_code == 0
    trace = session.trace_path.read_text()
    assert "[RES]" in trace
    assert "hello wrap" in trace


@pytest.mark.integration
def test_wrap_runner_pty_spawn_emits_term_reset_prelude(tmp_path, capsys):
    """HATS-215: each session emits DEC-mode reset prelude before child spawn.

    Without this prelude, kitty-keyboard stack pushed by a previous Claude
    session leaks across runs in the same tmux pane and Enter starts inserting
    newlines instead of submitting messages.
    """
    from ai_hats.runtime import _TERM_RESET_PRELUDE, WrapRunner

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="t", session_dir=session_dir)
    tracer = SidecarTracer(session)

    runner = object.__new__(WrapRunner)
    runner._pty_spawn(["true"], {}, tracer)

    captured = capsys.readouterr().out
    assert _TERM_RESET_PRELUDE in captured


@pytest.mark.integration
def test_pty_spawn_does_not_pollute_parent_environ(tmp_path):
    """HATS-713: _pty_spawn must pass per-session env to the child via
    PtyProcess.spawn(env=...), NOT by mutating the parent os.environ.

    Two guarantees in one slice:
      - the child still receives the per-session env (guard against a regression
        that deletes the mutation loop but forgets to pass env=);
      - the parent os.environ is left untouched (the actual bug — stale keys
        used to leak into the finalize pipeline, SESSION_END hooks, and the next
        in-process session).
    """
    from ai_hats.runtime import WrapRunner

    sentinel = "AI_HATS_HATS713_SENTINEL"
    value = "leak-canary"
    assert sentinel not in os.environ, "precondition: sentinel must start absent"

    session_dir = tmp_path / "s"
    session_dir.mkdir()
    session = Session(session_id="t", session_dir=session_dir)
    tracer = SidecarTracer(session)
    runner = object.__new__(WrapRunner)

    cmd = [sys.executable, "-c", f"import os;print(os.environ.get('{sentinel}',''))"]
    try:
        exit_code = runner._pty_spawn(cmd, {sentinel: value}, tracer)

        assert exit_code == 0
        # Child saw the per-session env.
        assert value in session.trace_path.read_text()
        # Parent env was NOT mutated (RED on the pre-fix code).
        assert sentinel not in os.environ
    finally:
        # Defensive: if the fix regresses, don't leak the sentinel into siblings.
        os.environ.pop(sentinel, None)


def test_master_read_dumps_raw_bytes_pre_strip(tmp_path, monkeypatch):
    """HATS-220: pty_raw.log preserves CSI escapes that strip_ansi erases.

    Required for diagnosing the recurring Enter-as-newline regression — we need
    to see kitty-keyboard pushes/pops and other DEC modes that the existing
    trace.log throws away. Opt-in via AI_HATS_PTY_RAW_DUMP=1.
    """
    monkeypatch.setenv("AI_HATS_PTY_RAW_DUMP", "1")
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[>1u\x1b[?2004hhello\x1b[<u")

    tracer.make_master_read()(fd)

    raw = session.pty_raw_path.read_bytes()
    assert b"<<" in raw
    assert b"\x1b[>1u" in raw  # kitty-keyboard push survived
    assert b"\x1b[?2004h" in raw  # bracketed paste enable survived
    assert b"\x1b[<u" in raw  # kitty-keyboard pop survived
    # And trace.log still has these stripped
    trace = session.trace_path.read_text()
    assert "\x1b" not in trace


def test_stdin_read_dumps_raw_bytes_with_direction(tmp_path, monkeypatch):
    """HATS-220: stdin path dumps with `>>` marker so direction is unambiguous."""
    monkeypatch.setenv("AI_HATS_PTY_RAW_DUMP", "1")
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[13u")  # raw "Enter" in kitty-keyboard form

    tracer.make_stdin_read()(fd)

    raw = session.pty_raw_path.read_bytes()
    assert b">>" in raw
    assert b"\x1b[13u" in raw


def test_raw_dump_disabled_by_default(tmp_path):
    """HATS-220: pty_raw.log is opt-in — must NOT be created without env flag."""
    session = make_test_session(tmp_path)
    tracer = SidecarTracer(session)
    fd = pipe_with(b"\x1b[>1u hello\x1b[<u")

    tracer.make_master_read()(fd)

    assert not session.pty_raw_path.exists()



# --- HATS-442: composition snapshot ---


def _sample_composition() -> dict:
    return {
        "traits": ["trait-base", "personal-workflow"],
        "rules": ["global_rule_resource_hygiene"],
        "skills": ["design-minimalism"],
        "provenance": {
            "traits": {
                "trait-base": "built-in",
                "personal-workflow": "global",
            },
            "rules": {"global_rule_resource_hygiene": "built-in"},
            "skills": {"design-minimalism": "built-in"},
        },
    }


def test_init_audit_without_composition_is_backwards_compatible(tmp_path):
    """Old callers (no composition kwarg) work identically — no Composition
    section in audit.md, no composition key in metrics.json."""
    session = make_test_session(tmp_path)
    session.init_audit(role="maintainer", provider="claude", model="sonnet-4-6")
    body = session.audit_path.read_text()
    assert "## Composition" not in body
    session.finalize_audit({"turns": 1, "tool_calls": 0})
    import json
    metrics = json.loads(session.metrics_path.read_text())
    assert "composition" not in metrics


def test_init_audit_with_composition_renders_section(tmp_path):
    session = make_test_session(tmp_path)
    session.init_audit(
        role="maintainer",
        provider="claude",
        composition=_sample_composition(),
    )
    body = session.audit_path.read_text()
    assert "## Composition" in body
    # Traits line tags source layers explicitly.
    assert "trait-base (built-in)" in body
    assert "personal-workflow (global)" in body
    assert "global_rule_resource_hygiene (built-in)" in body
    assert "design-minimalism (built-in)" in body
    # Order: composition section sits BEFORE the events table (so reviewers
    # see context before the timeline).
    assert body.index("## Composition") < body.index("## Events")


def test_finalize_audit_includes_composition_in_metrics(tmp_path):
    session = make_test_session(tmp_path)
    composition = _sample_composition()
    session.init_audit(role="maintainer", provider="claude", composition=composition)
    session.finalize_audit({"turns": 3, "tool_calls": 12})
    import json
    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["composition"] == composition
    # Existing fields preserved.
    assert metrics["turns"] == 3
    assert metrics["tool_calls"] == 12


def test_composition_section_omits_empty_buckets(tmp_path):
    """A role with no skills (e.g. test-agent) shouldn't render an empty
    'Skills:' line — the render skips empty buckets."""
    session = make_test_session(tmp_path)
    session.init_audit(
        role="bare",
        provider="claude",
        composition={
            "traits": ["t1"],
            "rules": [],
            "skills": [],
            "provenance": {"traits": {"t1": "built-in"}, "rules": {}, "skills": {}},
        },
    )
    body = session.audit_path.read_text()
    assert "- **Traits**:" in body
    assert "- **Rules**:" not in body
    assert "- **Skills**:" not in body


def test_composition_provenance_defaults_to_built_in(tmp_path):
    """A trait listed in the effective list but without an entry in the
    provenance map falls back to 'built-in' — safe-default for partial maps."""
    session = make_test_session(tmp_path)
    session.init_audit(
        role="r",
        provider="claude",
        composition={
            "traits": ["unmapped-trait"],
            "rules": [],
            "skills": [],
            "provenance": {"traits": {}, "rules": {}, "skills": {}},
        },
    )
    body = session.audit_path.read_text()
    assert "unmapped-trait (built-in)" in body


def test_audit_writer_preserves_composition_after_rebuild(tmp_path, monkeypatch):
    """HATS-442 follow-up: AuditWriter.build rewrites audit.md from
    JSONL/trace; the composition snapshot written by init_audit must
    survive the rebuild (read back from metrics.json which preserves
    existing keys via existing.update)."""
    import json
    from ai_hats.observe import AuditWriter

    session = make_test_session(tmp_path)
    composition = _sample_composition()
    session.init_audit(role="maintainer", provider="claude", composition=composition)
    session.finalize_audit({"role": "maintainer", "provider": "claude", "turns": 1, "tool_calls": 0})

    # Sanity — metrics.json carries the composition before rebuild.
    metrics = json.loads(session.metrics_path.read_text())
    assert metrics["composition"] == composition

    # No JSONL → AuditWriter falls back to trace.log path (empty in this fixture).
    # The fallback still calls _format_audit which must surface composition.
    session.trace_path.write_text("")  # empty trace
    AuditWriter().build(session, jsonl_path=None, keep_raw=False)

    rebuilt = session.audit_path.read_text()
    assert "## Composition" in rebuilt, "composition section lost during AuditWriter rebuild"
    assert "trait-base (built-in)" in rebuilt
    assert "personal-workflow (global)" in rebuilt


def test_build_folds_transcript_when_no_turns(tmp_path):
    """HATS-682: SDK sub-agents (e.g. isolation=discard hypothesis-intake) leave
    a non-empty transcript.txt (stdout) but no reachable JSONL and no trace.log,
    so AuditWriter.build() parses zero structured turns. The real work must not
    be lost — the transcript content is folded into the audit body."""
    from ai_hats.observe import AuditWriter

    session = make_test_session(tmp_path)
    session.init_audit(role="hypothesis-intake", provider="claude")
    session.finalize_audit({"role": "hypothesis-intake", "provider": "claude"})

    draft = "BEGIN_INTAKE_RESULT\naction: create\ndraft: editable install mismatch\nEND_INTAKE_RESULT"
    (session.session_dir / TRANSCRIPT_TXT).write_text(draft)
    session.trace_path.write_text("")  # empty trace → no parseable turns

    AuditWriter().build(session, jsonl_path=None, keep_raw=False)
    audit = session.audit_path.read_text()

    assert "## Transcript" in audit, "transcript fallback section missing"
    assert "BEGIN_INTAKE_RESULT" in audit
    assert "END_INTAKE_RESULT" in audit


def test_build_skips_transcript_when_turns_present(tmp_path):
    """HATS-682 R2: when the JSONL yields real structured turns, the transcript
    fallback must NOT fire — no duplication of content already rendered as
    👤/👾 turns."""
    from ai_hats.observe import AuditWriter

    session = make_test_session(tmp_path)
    session.init_audit(role="assistant", provider="claude")
    session.finalize_audit({"role": "assistant", "provider": "claude"})

    jsonl = tmp_path / "conversation.jsonl"
    jsonl.write_text(
        '{"type": "user", "timestamp": "2026-06-06T00:00:00Z", '
        '"message": {"role": "user", "content": [{"type": "text", "text": "real question"}]}}\n'
        '{"type": "assistant", "timestamp": "2026-06-06T00:00:01Z", '
        '"message": {"role": "assistant", "content": [{"type": "text", "text": "real answer"}], '
        '"usage": {"input_tokens": 10, "output_tokens": 5}}}\n'
    )

    (session.session_dir / TRANSCRIPT_TXT).write_text("SHOULD_NOT_APPEAR_TWICE")

    AuditWriter().build(session, jsonl_path=jsonl)
    audit = session.audit_path.read_text()

    assert "👤 real question" in audit
    assert "## Transcript (raw" not in audit, "fallback fired despite parsed turns"
    assert "SHOULD_NOT_APPEAR_TWICE" not in audit


def test_build_no_turns_no_transcript_is_metaonly(tmp_path):
    """HATS-682 R3: no turns AND no transcript.txt (genuinely empty/aborted) →
    meta-only audit, no Transcript section, no crash."""
    from ai_hats.observe import AuditWriter

    session = make_test_session(tmp_path)
    session.init_audit(role="maintainer", provider="claude")
    session.finalize_audit({"role": "maintainer", "provider": "claude"})
    session.trace_path.write_text("")  # empty trace, no transcript.txt

    AuditWriter().build(session, jsonl_path=None, keep_raw=False)
    audit = session.audit_path.read_text()

    assert "## Transcript" not in audit
    assert "## Metrics" in audit


def test_extract_user_text_filters_skill_body_injection():
    """HATS-666: a Skill invocation re-injects the full SKILL.md as a user
    text message ("Base directory for this skill: …"). It is 100% redundant
    with the `🔧 Skill: <name>` tool line and must be filtered like a
    tool_result, not rendered as a verbatim 👤 turn."""
    from ai_hats_observe.parsers.claude import ClaudeParser

    skill_body = (
        "Base directory for this skill: /Users/x/.agent/ai-hats/skills/backlog-manager\n\n"
        "# Backlog Manager\n\nOrchestrate the lifecycle ...\n" + ("blah " * 2000)
    )
    assert ClaudeParser._extract_user_text(skill_body) is None
    # A real user message is untouched.
    assert ClaudeParser._extract_user_text("давай возьмем 666 задачку") == "давай возьмем 666 задачку"


def test_format_audit_preserves_full_user_input(tmp_path):
    """HATS-683: audit generation is LOSSLESS — `_format_audit` renders
    `user_input` in full, no per-turn cap. (Audit *size* is managed at the
    delivery layer — `_truncate_audit`, HATS-684 — not by destroying the
    canonical record. The skill-body filter, HATS-666, stays the lossless way
    to drop pure noise.)"""
    from ai_hats.observe import AuditWriter, Turn

    session = make_test_session(tmp_path)
    big = "Z" * 50000
    turns = [Turn(timestamp="12:00:00", user_input=big, response="ok")]
    out = AuditWriter()._format_audit(session, turns)

    assert big in out, "user_input must be rendered in full (no cap)"
    assert "chars truncated" not in out, "no truncation marker — generation is lossless"


# --- HATS-735: metrics.json guarded reads + atomic write ---

def test_audit_build_survives_corrupt_metrics_json(tmp_path):
    """A torn/corrupt metrics.json must not crash the audit build.

    Before the read-guard, _format_audit/_write_metrics did a raw json.loads
    that raised JSONDecodeError; make_audit swallowed it into a logger.warning
    and the structured audit silently vanished for that session.
    """
    from ai_hats.observe import AuditWriter

    session = make_test_session(tmp_path)
    session.metrics_path.write_text("{ this is not valid json ]]")  # torn file
    session.trace_path.write_text("")

    # Must not raise; audit.md must still be produced.
    AuditWriter().build(session, jsonl_path=None, keep_raw=False)
    assert session.audit_path.exists()
    assert "# Session Audit" in session.audit_path.read_text()


def test_finalize_audit_metrics_write_is_atomic(tmp_path, monkeypatch):
    """A crash during the metrics.json write must not truncate an existing file.

    Fails under open('w')+json.dump (truncates before json.dump writes a byte);
    passes once the write routes through atomic_io (serialize, then atomic replace).
    """
    from ai_hats_core import atomic_io

    session = make_test_session(tmp_path)
    session.finalize_audit({"turns": 1, "tool_calls": 2})
    original = session.metrics_path.read_text()

    def boom(src, dst):
        raise OSError("simulated crash before rename completes")

    monkeypatch.setattr(atomic_io.os, "replace", boom)
    with pytest.raises(OSError):
        session.finalize_audit({"turns": 999, "tool_calls": 999})

    assert session.metrics_path.read_text() == original  # never truncated
    orphans = [
        f for f in session.metrics_path.parent.iterdir()
        if f.name.startswith(".metrics.json.")
    ]
    assert orphans == []
