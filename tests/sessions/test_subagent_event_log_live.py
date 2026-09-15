"""A sub-agent session writes its own events.jsonl while it runs (HATS-1967).

The runner mints the surface's session id, so the record can be found before
the surface reports which id it chose; the SDK call is the only thing stubbed,
and the stub writes the transcript exactly where a real ``claude`` would — under
the cwd it was handed and the id it was handed — so the real resolver is what
finds it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, claude_transcript_path
from ai_hats_observe.canonical import PromptReceived, RunEnded, RunStarted
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "transcripts" / "normal.jsonl"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    # Where claude keeps its transcripts, kept inside this test's sandbox.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    return project


def test_a_subagent_session_leaves_its_event_log_written_live(project: Path):
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.runtime import SubAgentRunner
    from ai_hats.surfaces.claude.provider import ClaudeSubagentEngine, ClaudeSurface
    from ai_hats_observe import SessionManager

    handed: list = []

    def fake_sdk(options, message, *, timeout_s, on_message=None):
        # A real surface writes its record under the cwd and id it was handed.
        transcript = claude_transcript_path(Path(options.cwd).resolve(), options.session_id)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        handed.append(options)
        return SimpleNamespace(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            error=None,
            claude_session_id=options.session_id,
            total_cost_usd=0.0,
            num_turns=1,
            stop_reason="end_turn",
        )

    class StubbedSdk(ClaudeSurface):
        # The SDK call is the engine's injected seam; the surface hands it in.
        def engine(self):
            return ClaudeSubagentEngine(self, run_blocking=fake_sdk)

    payload = replace(
        build_composition_payload(project, role_override="maintainer"), provider=StubbedSdk()
    )
    runner = SubAgentRunner(
        ProjectLayout.at(project),
        payload,
        session_mgr=SessionManager(project, runs_dir=ProjectLayout.at(project).sessions.runs),
    )

    session = runner.run(task="test task", isolation_mode="none")

    (options,) = handed
    assert options.session_id, "the runner must hand the SDK the id it minted"
    events = list(read_events(session.session_dir / EVENT_LOG_JSONL))
    expected = list(
        ClaudeTranscriptReader(
            claude_transcript_path(Path(options.cwd).resolve(), options.session_id)
        ).read()
    )
    assert isinstance(events[0], RunStarted) and isinstance(events[-1], RunEnded)
    assert events[1:-1] == expected
    assert len(events) > 5, "fixture too thin to prove the record"
    metrics = json.loads(session.metrics_path.read_text(encoding="utf-8"))
    assert metrics["claude_session_id"] == options.session_id


def test_a_quota_warning_the_stream_alone_carries_reaches_the_log_as_it_happens(project: Path):
    """``allowed_warning`` appears in zero transcripts of the measured corpus:
    only the SDK stream says the wall is coming. The engine hands every stream
    message to a seam; a rate-limit message is appended to the session's log
    with the moment it arrived, so the one signal that comes in time to change
    what a caller does is on the record — beside the transcript's events, which
    the stream never duplicates."""
    from claude_agent_sdk import RateLimitEvent, RateLimitInfo

    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.runtime import SubAgentRunner
    from ai_hats.surfaces.claude.provider import ClaudeSubagentEngine, ClaudeSurface
    from ai_hats_observe import SessionManager
    from ai_hats_observe.canonical import HarnessActionRequired, Notice, WorthRecording

    def fake_sdk(options, message, *, timeout_s, on_message):
        transcript = claude_transcript_path(Path(options.cwd).resolve(), options.session_id)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        # What the stream carries: the transcript's own records (never re-said
        # through this seam), then the two rate-limit states, then a message
        # the seam must ignore.
        for status in ("allowed_warning", "rejected"):
            on_message(
                RateLimitEvent(
                    rate_limit_info=RateLimitInfo(
                        status=status, resets_at=1767225600, utilization=0.97
                    ),
                    uuid="u1",
                    session_id=options.session_id,
                )
            )
        on_message(SimpleNamespace(kind="something else"))
        return SimpleNamespace(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            error=None,
            claude_session_id=options.session_id,
            total_cost_usd=0.0,
            num_turns=1,
            stop_reason="end_turn",
        )

    class StubbedSdk(ClaudeSurface):
        def engine(self):
            return ClaudeSubagentEngine(self, run_blocking=fake_sdk)

    payload = replace(
        build_composition_payload(project, role_override="maintainer"), provider=StubbedSdk()
    )
    runner = SubAgentRunner(
        ProjectLayout.at(project),
        payload,
        session_mgr=SessionManager(project, runs_dir=ProjectLayout.at(project).sessions.runs),
    )

    session = runner.run(task="test task", isolation_mode="none")

    events = list(read_events(session.session_dir / EVENT_LOG_JSONL))
    signals = [e for e in events if isinstance(e, (Notice, HarnessActionRequired))]
    assert [(type(e).__name__, str(e.reason), e.source) for e in signals] == [
        ("Notice", "approaching_limit", "claude/sdk"),
        ("HarnessActionRequired", "wait", "claude/sdk"),
    ]
    assert signals[0].reason is WorthRecording.APPROACHING_LIMIT
    assert signals[1].retry_after == 1767225600
    # stamped at receipt: the stream itself carries no time
    assert all(e.ts is not None for e in signals)
    # POSITIVE CONTROL: the transcript's own content is in the file once, from
    # the transcript — the seam added nothing but the two signals
    assert len([e for e in events if isinstance(e, PromptReceived)]) == 1
