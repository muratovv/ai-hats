"""E2E baseline for `python -m ai_hats.cli.reflect_session_main` (background).

Covers the harness-check + meta-proposal filing layer that wraps
SessionReviewRunner.run. After HATS-269 migration, runner.run is
replaced by pipeline run, but harness-check + meta-proposal logic must
remain untouched.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_hats.cli import reflect_session_main as rsm
from ai_hats.retro.session_review_runner import SessionReviewError



def _read_proposals(pd: Path) -> list[dict]:
    out: list[dict] = []
    for f in (pd / ".agent" / "backlog" / "proposals").glob("*.yaml"):
        out.append(yaml.safe_load(f.read_text()))
    return out


def test_main_happy_no_proposal(project_dir: Path, mock_runners, monkeypatch):
    """Runner succeeds, harness-check passes → exit 0, no meta-proposal."""
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rc = rsm.main()
    assert rc == 0
    assert _read_proposals(project_dir) == []


def test_main_runner_error_files_proposal(
    project_dir: Path, mock_runners, monkeypatch,
):
    """SessionReviewError → harness-check sees missing output → meta-proposal."""

    class _FailingRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1):
            raise SessionReviewError("provider down")

    monkeypatch.setattr(rsm, "SessionReviewRunner", _FailingRunner)
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rc = rsm.main()
    assert rc == 2
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1
    p = proposals[0]
    assert p["category"] == "process"
    assert p["target"] == "session-reviewer"
    assert p["failed_session_id"] == "x-1"


def test_main_incomplete_yaml_files_proposal(
    project_dir: Path, mock_runners, monkeypatch,
):
    """Runner succeeds but writes empty/invalid YAML → harness-check fails."""

    class _BadRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1):
            out = (
                project_dir / ".agent" / "retrospectives" / "sessions"
                / f"{sid}.md"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("")  # empty
            return out

    monkeypatch.setattr(rsm, "SessionReviewRunner", _BadRunner)
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rc = rsm.main()
    assert rc == 2
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1
    assert "missing or empty" in proposals[0]["description"].lower()


def test_main_runner_error_dedups_proposal(
    project_dir: Path, mock_runners, monkeypatch,
):
    """Same failed_session_id → second run does not file duplicate proposal."""

    class _FailingRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1):
            raise SessionReviewError("fail")

    monkeypatch.setattr(rsm, "SessionReviewRunner", _FailingRunner)
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rsm.main()
    rsm.main()  # second invocation
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1, "dedup must skip 2nd proposal for same session"
