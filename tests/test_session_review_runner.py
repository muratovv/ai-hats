"""Unit tests for SessionReviewRunner (HATS-252).

Covers the pure-Python machinery: forbidden-key rejection, analysis-shape
validation, the merge step, and a happy-path round trip with a stubbed
SubAgentRunner.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ai_hats.retro.facts import SessionFacts
from ai_hats.retro.session_review_runner import (
    SessionReviewError,
    SessionReviewRunner,
)
from ai_hats.retro.session_review_schema import SessionReviewV1
from ai_hats.retro.common import SessionArtifacts, SessionLinks, SessionMetrics
from ai_hats.retro.loader import load


SID = "20260506-100000-1"


# ---- helpers ----


def _facts(sid: str = SID) -> SessionFacts:
    metrics = SessionMetrics(exit_code=0, turns=4, tool_calls=10)
    artifacts = SessionArtifacts(files_changed=["a.py"], commits=[], tasks_closed=[])
    links = SessionLinks(audit="../../../.gitlog/session_X/audit.md")
    return SessionFacts(
        session_id=sid,
        project="test",
        role="assistant",
        date=datetime(2026, 5, 6).date(),
        metrics=metrics,
        artifacts=artifacts,
        links=links,
        session_start=datetime(2026, 5, 6, 10, tzinfo=timezone.utc),
        session_end=datetime(2026, 5, 6, 11, tzinfo=timezone.utc),
    )


def _add_active_hyp(project_dir: Path, hyp_id: str = "HYP-001") -> None:
    hyps_dir = project_dir / ".agent" / "hypotheses"
    hyps_dir.mkdir(parents=True, exist_ok=True)
    (hyps_dir / f"{hyp_id}.yaml").write_text(
        "id: " + hyp_id + "\n"
        "title: t\nstatus: active\ncreated: '2026-05-01'\n"
        "source_task: TASK-001\nhypothesis: a\nvalidation_log: []\n"
    )


# ---- _check_allowed_keys ----


def test_check_allowed_keys_accepts_canonical(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    runner._check_allowed_keys({
        "summary": "x",
        "observations": [],
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    })


def test_check_allowed_keys_rejects_facts(tmp_path: Path) -> None:
    """LLM is forbidden from emitting runner-injected fields."""
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="forbidden"):
        runner._check_allowed_keys({"summary": "x", "metrics": {}})


# ---- _validate_analysis_shape ----


def test_validate_analysis_shape_requires_summary(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="summary"):
        runner._validate_analysis_shape({"summary": ""}, SID)


def test_validate_analysis_shape_requires_active_hyp_coverage(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-042")
    runner = SessionReviewRunner(tmp_path)
    with pytest.raises(ValueError, match="HYP-042"):
        runner._validate_analysis_shape(
            {"summary": "ok", "hypothesis_verdicts": []}, SID,
        )


def test_validate_analysis_shape_passes_with_full_coverage(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-042")
    runner = SessionReviewRunner(tmp_path)
    runner._validate_analysis_shape(
        {
            "summary": "ok",
            "hypothesis_verdicts": [
                {
                    "hyp_id": "HYP-042",
                    "verdict": "inconclusive",
                    "evidence": "no signal",
                    "recommendation": "keep",
                },
            ],
        },
        SID,
    )


# ---- _merge ----


def test_merge_produces_valid_review(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    review = runner._merge(_facts(), {
        "summary": "did stuff",
        "observations": ["obs1"],
        "hypothesis_verdicts": [],
        "proposal_actions": [],
        "self_problems": [],
    })
    assert isinstance(review, SessionReviewV1)
    assert review.metrics.turns == 4
    assert review.artifacts.files_changed == ["a.py"]
    assert review.summary == "did stuff"


def test_save_round_trips_through_loader(tmp_path: Path) -> None:
    runner = SessionReviewRunner(tmp_path)
    review = runner._merge(_facts(), {"summary": "s"})
    path = runner._save(review)
    loaded, _body = load(path)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.session_id == SID


# ---- happy-path with stubbed SubAgentRunner ----


class _FakeSubAgentSession:
    def __init__(
        self,
        transcript_text: str,
        session_dir: Path,
        *,
        session_id: str = "20260101-000000-2",
        write_transcript: bool = True,
        metrics: dict | None = None,
        reasoning: str | None = None,
    ) -> None:
        self.session_dir = session_dir
        self.session_id = session_id
        self.metrics_path = session_dir / "metrics.json"
        if write_transcript:
            (session_dir / "transcript.txt").write_text(transcript_text)
        if metrics is not None:
            self.metrics_path.write_text(json.dumps(metrics))
        if reasoning is not None:
            (session_dir / "reasoning.log").write_text(reasoning)


class _FakeSubAgentRunner:
    """Minimal stub matching SubAgentRunner.run signature."""

    def __init__(
        self,
        transcript_text: str,
        scratch: Path,
        *,
        write_transcript: bool = True,
        metrics: dict | None = None,
        reasoning: str | None = None,
    ) -> None:
        self._transcript = transcript_text
        self._scratch = scratch
        self._write_transcript = write_transcript
        self._metrics = metrics
        self._reasoning = reasoning

    def run(self, **_kw):  # noqa: ANN003 — test stub matches keyword API
        sdir = self._scratch / "sub-session"
        sdir.mkdir(exist_ok=True)
        return _FakeSubAgentSession(
            self._transcript,
            sdir,
            write_transcript=self._write_transcript,
            metrics=self._metrics,
            reasoning=self._reasoning,
        )


def _stub_facts(monkeypatch, project_dir: Path) -> None:
    from ai_hats.retro import session_review_runner as mod

    monkeypatch.setattr(mod, "compute_facts", lambda pd, sid: _facts(sid))


def test_run_writes_artifact_and_round_trips(tmp_path: Path, monkeypatch) -> None:
    _stub_facts(monkeypatch, tmp_path)

    transcript = (
        "noise BEGIN_REFLECT_SESSION_RETRO\n"
        + yaml.safe_dump({
            "summary": "what happened",
            "observations": ["o1"],
            "hypothesis_verdicts": [],
            "proposal_actions": [],
            "self_problems": [],
        })
        + "\nEND_REFLECT_SESSION_RETRO trailing\n"
    )
    fake_runner = _FakeSubAgentRunner(transcript, tmp_path)

    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    path = runner.run(SID)

    loaded, _body = load(path)
    assert isinstance(loaded, SessionReviewV1)
    assert loaded.summary == "what happened"
    assert loaded.metrics.turns == 4  # facts merged in


def test_run_raises_session_review_error_on_invalid_llm_output(
    tmp_path: Path, monkeypatch,
) -> None:
    _stub_facts(monkeypatch, tmp_path)
    transcript = (
        "BEGIN_REFLECT_SESSION_RETRO\n"
        + yaml.safe_dump({"summary": ""})  # empty summary → fails validation
        + "\nEND_REFLECT_SESSION_RETRO\n"
    )
    fake_runner = _FakeSubAgentRunner(transcript, tmp_path)
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError):
        runner.run(SID, max_retries=0)


# ---- HATS-271: empty sub-agent transcript surfaces real diagnostics ----


def test_run_surfaces_subagent_failure_when_transcript_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """Sub-agent crashed before writing transcript.txt → must NOT loop on
    'Empty frontmatter'; must surface exit_code/error from metrics.json so
    the harness records a meaningful cause in retro.log."""
    _stub_facts(monkeypatch, tmp_path)
    fake_runner = _FakeSubAgentRunner(
        "", tmp_path,
        write_transcript=False,
        metrics={"exit_code": 124, "timed_out": True},
        reasoning="claude: request timed out\n",
    )
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError) as excinfo:
        runner.run(SID, max_retries=2)

    msg = str(excinfo.value)
    assert "sub-agent produced no output" in msg
    assert "exit_code=124" in msg
    assert "timed_out=True" in msg
    assert "request timed out" in msg
    # Critically, the misleading "Empty frontmatter" wording from the
    # validator path must NOT be the surface error here.
    assert "Empty frontmatter" not in msg


def test_run_surfaces_subagent_failure_when_transcript_blank(
    tmp_path: Path, monkeypatch,
) -> None:
    """transcript.txt exists but contains only whitespace — same failure
    mode as missing file: do not retry, surface diagnostics."""
    _stub_facts(monkeypatch, tmp_path)
    fake_runner = _FakeSubAgentRunner(
        "   \n\n",  # whitespace-only
        tmp_path,
        write_transcript=True,
        metrics={"exit_code": 1},
    )
    runner = SessionReviewRunner(tmp_path, subagent_runner=fake_runner)
    with pytest.raises(SessionReviewError) as excinfo:
        runner.run(SID, max_retries=3)

    msg = str(excinfo.value)
    assert "sub-agent produced no output" in msg
    assert "exit_code=1" in msg
    assert "Empty frontmatter" not in msg
