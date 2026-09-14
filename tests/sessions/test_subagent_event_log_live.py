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

    def fake_sdk(options, message, *, timeout_s):
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
    assert events == expected
    assert len(events) > 3, "fixture too thin to prove the record"
    metrics = json.loads(session.metrics_path.read_text(encoding="utf-8"))
    assert metrics["claude_session_id"] == options.session_id
