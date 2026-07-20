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
from ai_hats.harness.errors import HarnessTimeoutError, HarnessZeroOutputError
from ai_hats.retro.session_review_runner import SessionReviewError
from ai_hats.paths import proposals_dir



def _read_proposals(pd: Path) -> list[dict]:
    # Meta-proposals are dir-per-card rack cards post-HATS-1044; category/target/
    # description/failed_session_id ride top-level, so the raw dict still asserts.
    out: list[dict] = []
    for f in (proposals_dir(pd)).glob("*/task.yaml"):
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
        def run(self, sid, max_retries=1, harness_policy=None):
            raise SessionReviewError("provider down")

    monkeypatch.setattr("ai_hats.retro.session_review_runner.SessionReviewRunner", _FailingRunner)
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
        def run(self, sid, max_retries=1, harness_policy=None):
            out = (
                project_dir / ".agent" / "retrospectives" / "sessions"
                / f"{sid}.md"
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text("")  # empty
            return out

    monkeypatch.setattr("ai_hats.retro.session_review_runner.SessionReviewRunner", _BadRunner)
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
        def run(self, sid, max_retries=1, harness_policy=None):
            raise SessionReviewError("fail")

    monkeypatch.setattr("ai_hats.retro.session_review_runner.SessionReviewRunner", _FailingRunner)
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rsm.main()
    rsm.main()  # second invocation
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1, "dedup must skip 2nd proposal for same session"


# ---- HATS-378 Phase 3: harness-incident routing ----


def test_main_harness_timeout_routes_to_harness_incident(
    project_dir: Path, mock_runners, monkeypatch,
):
    """HarnessTimeoutError → meta-PROP with target=harness-incident, NOT
    session-reviewer (so the inbox doesn't blame the role for a harness
    failure)."""

    class _TimingOutRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise HarnessTimeoutError(
                "sub-1", "exit_code=124; timed_out=True",
            )

    monkeypatch.setattr(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        _TimingOutRunner,
    )
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])

    rc = rsm.main()

    assert rc == 2
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1
    p = proposals[0]
    assert p["category"] == "process"
    assert p["target"] == "harness-incident"
    assert p["failed_session_id"] == "x-1"
    assert "harness incident" in p["title"].lower()


def test_main_zero_output_routes_to_harness_incident(
    project_dir: Path, mock_runners, monkeypatch,
):
    """HarnessZeroOutputError follows the same path as HarnessTimeoutError —
    both subclass HarnessReliabilityError."""

    class _ZeroOutputRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise HarnessZeroOutputError(
                "sub-1", "tokens.output=0; tool_calls=0",
            )

    monkeypatch.setattr(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        _ZeroOutputRunner,
    )
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-2"])

    rc = rsm.main()

    assert rc == 2
    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1
    assert proposals[0]["target"] == "harness-incident"


def test_main_harness_incident_dedup_isolated_from_session_reviewer(
    project_dir: Path, mock_runners, monkeypatch,
):
    """Same session_id under different targets must not dedup against
    each other — they describe different failure facets."""

    # First: a session-reviewer error files target=session-reviewer
    class _ValidationRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise SessionReviewError("schema fail")

    monkeypatch.setattr(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        _ValidationRunner,
    )
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-1"])
    rsm.main()

    # Then: a harness incident for the same session files target=harness-incident
    class _TimeoutRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise HarnessTimeoutError("sub-1", "timed_out=True")

    monkeypatch.setattr(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        _TimeoutRunner,
    )
    rsm.main()

    proposals = _read_proposals(project_dir)
    targets = {p["target"] for p in proposals}
    assert targets == {"session-reviewer", "harness-incident"}, (
        "both targets must coexist for same session_id"
    )


def test_main_harness_incident_dedup_same_target(
    project_dir: Path, mock_runners, monkeypatch,
):
    """Two harness-incident failures for the same session → only one PROP."""

    class _TimingOutRunner:
        def __init__(self, _pd): pass
        def run(self, sid, max_retries=1, harness_policy=None):
            raise HarnessTimeoutError("sub-1", "timed_out=True")

    monkeypatch.setattr(
        "ai_hats.retro.session_review_runner.SessionReviewRunner",
        _TimingOutRunner,
    )
    monkeypatch.setattr("sys.argv", ["reflect_session_main", "x-9"])

    rsm.main()
    rsm.main()  # second invocation — should dedup

    proposals = _read_proposals(project_dir)
    assert len(proposals) == 1
    assert proposals[0]["target"] == "harness-incident"
