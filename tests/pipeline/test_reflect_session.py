"""E2E baseline for `ai-hats reflect session` foreground — HATS-269."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.retro.session_review_runner import SessionReviewError



def test_reflect_session_foreground_happy(
    project_dir: Path, mock_runners,
):
    res = CliRunner().invoke(
        main, ["reflect", "session", "--session", "20260101-000000-1"],
    )
    assert res.exit_code == 0, res.output
    assert mock_runners["session_review_calls"] == [
        ("20260101-000000-1", 1)
    ]
    assert "session review saved to" in res.output


def test_reflect_session_foreground_failure(
    project_dir: Path, mock_runners, monkeypatch,
):
    """SessionReviewError propagates to exit 2."""
    class _FailingRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise SessionReviewError("synthetic fail")

    # Patch at the source — the run_session_review pipeline step does a
    # lazy `from ...retro.session_review_runner import SessionReviewRunner`
    # so the source-module attribute is what matters.
    import ai_hats.retro.session_review_runner as srr
    monkeypatch.setattr(srr, "SessionReviewRunner", _FailingRunner)

    res = CliRunner().invoke(
        main, ["reflect", "session", "--session", "x-1"],
    )
    assert res.exit_code == 2
    assert "session-reviewer failed" in res.output


def test_reflect_session_foreground_max_retries(
    project_dir: Path, mock_runners,
):
    res = CliRunner().invoke(
        main, ["reflect", "session", "--session", "x-1", "--max-retries", "3"],
    )
    assert res.exit_code == 0
    assert mock_runners["session_review_calls"] == [("x-1", 3)]
