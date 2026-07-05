"""HATS-513 — pipeline-integration tests for `reflect hypothesis`.

Layer: pipeline-integration. The two-phase pipeline orchestration is
exercised through the real CLI (`ai-hats reflect hypothesis`) + real
PipelineHarness + real step graph. The runner boundary (WrapRunner /
SubAgentRunner) is mocked so no real Claude session is spawned, but
extract_marker actually reads the (stubbed) transcript and
save_artifact actually writes to disk — the contract between Phase 1
and Phase 2 is what we're testing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import hypotheses_dir, proposals_dir, retros_dir
from ai_hats.paths import TRACE_LOG, TRANSCRIPT_TXT


def _make_hyp(pd: Path, hyp_id: str):
    body = {
        "id": hyp_id, "title": f"hyp-{hyp_id}",
        "status": "active", "created": "2026-01-01",
        "source_task": "HATS-001", "hypothesis": "h",
        "validation_log": [],
        "success_criterion": "x",
        "observation_window": "5 sessions",
    }
    (hypotheses_dir(pd) / f"{hyp_id}.yaml").write_text(yaml.safe_dump(body))


def _make_prop(pd: Path, pid: str):
    body = {
        "id": pid,
        "created": datetime(2026, 5, 4, tzinfo=timezone.utc).isoformat(),
        "title": f"title-{pid}", "category": "rule", "target": "x",
        "description": "d", "rationale": "r",
        "votes": [], "status": "open",
    }
    (proposals_dir(pd) / f"{pid}.yaml").write_text(yaml.safe_dump(body))


# --- helpers ----------------------------------------------------------------


def _seed_draft_transcript(session_dir: Path, body: str = "") -> Path:
    """Write a fake transcript.txt + trace.log with BEGIN/END markers,
    matching what a real judge-auditor session would emit."""
    session_dir.mkdir(parents=True, exist_ok=True)
    if not body:
        body = (
            "# Judge draft — 2026-05-26T12-00-00Z\n\n"
            "## Mode\n\ndraft (Phase 1 — judge-auditor)\n\n"
            "## Hypotheses\n\nHYP-001 — keep: low signal\n\n"
            "## Proposals\n\nPROP-001 — defer: uncited\n\n"
            "## Proposed mutations\n\n(none)\n"
        )
    framed = f"BEGIN_JUDGE_DRAFT\n{body}\nEND_JUDGE_DRAFT\n"
    (session_dir / TRANSCRIPT_TXT).write_text(framed)
    (session_dir / TRACE_LOG).write_text("(trace)")
    return session_dir / TRANSCRIPT_TXT


def _seed_report_trace(session_dir: Path) -> Path:
    """Write a fake trace.log with BEGIN_JUDGE/END_JUDGE markers, as a
    real judge HITL session would (WrapRunner trace)."""
    session_dir.mkdir(parents=True, exist_ok=True)
    body = (
        "# Judge report — 2026-05-26T12-30-00Z\n\n"
        "## Mode\n\nPhase 2 (HITL)\n\n"
        "## Hypotheses\n\nHYP-001 — kept\n"
    )
    framed = f"BEGIN_JUDGE\n{body}\nEND_JUDGE\n"
    (session_dir / TRACE_LOG).write_text(framed)
    return session_dir / TRACE_LOG


# --- dry-run ----------------------------------------------------------------


def test_dry_run_writes_handoff_no_pipeline(project_dir: Path, mock_runners):
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    res = CliRunner().invoke(
        main, ["reflect", "hypothesis", "--dry-run"]
    )
    assert res.exit_code == 0, res.output

    handoff_files = list(
        (retros_dir(project_dir) / "reflect-all").glob("*-handoff.md")
    )
    assert len(handoff_files) == 1

    # No pipeline launched at all
    assert mock_runners["wrap_calls"] == []
    assert mock_runners["sub_calls"] == []


# --- headless (Phase 1 only) -----------------------------------------------


def test_headless_runs_phase1_only(
    project_dir: Path, mock_runners, monkeypatch
):
    """`reflect hypothesis --headless` invokes SubAgentRunner with role
    judge-auditor and exits without touching WrapRunner."""
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    # Seed the stub session output so extract_marker captures a draft.
    from ai_hats.paths import runs_dir
    sub_session_dir = runs_dir(project_dir) / "session_sub-1"
    _seed_draft_transcript(sub_session_dir)

    res = CliRunner().invoke(main, ["reflect", "hypothesis", "--headless"])
    assert res.exit_code == 0, res.output

    # Phase 1 ran via SubAgentRunner; Phase 2 did NOT run.
    assert len(mock_runners["sub_calls"]) == 1
    assert mock_runners["wrap_calls"] == []

    call = mock_runners["sub_calls"][0]
    assert call["role_name"] == "judge-auditor"

    # Draft was persisted.
    draft_files = list(
        (retros_dir(project_dir) / "judge").glob("*-draft.md")
    )
    assert len(draft_files) == 1
    assert "HYP-001" in draft_files[0].read_text()


# --- full 2-phase ----------------------------------------------------------


def test_full_runs_both_phases(
    project_dir: Path, mock_runners
):
    """`reflect hypothesis` runs Phase 1 (SubAgent) then Phase 2 (Wrap)
    with role=judge and the draft body inlined in the preamble."""
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    # Seed both phases' fake outputs.
    from ai_hats.paths import runs_dir
    _seed_draft_transcript(runs_dir(project_dir) / "session_sub-1")
    _seed_report_trace(runs_dir(project_dir) / "session_wrap-1")

    res = CliRunner().invoke(main, ["reflect", "hypothesis"])
    assert res.exit_code == 0, res.output

    # Phase 1: SubAgent + judge-auditor
    assert len(mock_runners["sub_calls"]) == 1
    assert mock_runners["sub_calls"][0]["role_name"] == "judge-auditor"

    # Phase 2: Wrap + judge
    assert len(mock_runners["wrap_calls"]) == 1
    wcall = mock_runners["wrap_calls"][0]
    assert wcall["role"] == "judge"
    # The Phase 2 preamble (with draft inlined) is in extra_args[0]
    first_arg = wcall["extra_args"][0]
    assert "Phase 1 draft" in first_arg, (
        "Phase 2 prompt must include the inlined draft section"
    )
    assert "HYP-001" in first_arg, (
        "draft body (with HYP) must be substituted into preamble"
    )

    # Both artifacts persisted
    drafts = list((retros_dir(project_dir) / "judge").glob("*-draft.md"))
    reports = list((retros_dir(project_dir) / "judge").glob("*-report.md"))
    assert len(drafts) == 1
    assert len(reports) == 1


# --- fail-closed: Phase 1 failure aborts Phase 2 ---------------------------


def test_phase1_failure_aborts_phase2(
    project_dir: Path, mock_runners, monkeypatch
):
    """If Phase 1 sub-agent exits non-zero, Phase 2 must not run."""
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    # Re-bind SubAgentRunner to a failing stub for this test. The default
    # `mock_runners` stub returns exit_code=0; we need exit_code=1 to
    # exercise the fail-closed branch.
    from tests.pipeline.conftest import _StubSession
    import ai_hats.runtime as rt

    class _FailingSubAgentRunner:
        def __init__(self, _pd, _payload, *, session_mgr=None): pass

        def run(self, **kwargs):
            mock_runners["sub_calls"].append(kwargs)
            # exit_code=1 → CLI fail-closed branch
            return _StubSession(project_dir, "sub-1", exit_code=1)

    monkeypatch.setattr(rt, "SubAgentRunner", _FailingSubAgentRunner)

    res = CliRunner().invoke(main, ["reflect", "hypothesis"])
    assert res.exit_code != 0, (
        f"Phase 1 failure must propagate non-zero exit (output={res.output!r})"
    )

    # Phase 1 ran; Phase 2 did NOT.
    assert len(mock_runners["sub_calls"]) == 1
    assert mock_runners["wrap_calls"] == [], (
        "Phase 2 must not run when Phase 1 failed"
    )
    assert "Phase 2 aborted" in res.output


# --- fail-closed: Phase 1 succeeded but produced empty draft -------------


def test_phase1_empty_draft_aborts_phase2(
    project_dir: Path, mock_runners
):
    """Phase 1 exits clean but transcript has no BEGIN_JUDGE_DRAFT markers
    → `extract_marker` returns "" → `save_artifact` writes a zero-byte
    draft file. CLI must detect this and abort Phase 2 (otherwise a
    HITL session opens against an empty draft).

    This guards the structural gap reviewer flagged: `"saved_path" not
    in r1` is unreachable in normal flow because `save_artifact` always
    emits `saved_path` even for empty content.
    """
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    # Seed the sub-agent session output WITHOUT the BEGIN_JUDGE_DRAFT
    # markers. extract_marker → "", save_artifact writes empty file.
    from ai_hats.paths import runs_dir
    sub_session_dir = runs_dir(project_dir) / "session_sub-1"
    sub_session_dir.mkdir(parents=True, exist_ok=True)
    (sub_session_dir / TRANSCRIPT_TXT).write_text(
        "(judge-auditor produced free-form text without markers)\n"
    )
    (sub_session_dir / TRACE_LOG).write_text("(trace)")

    res = CliRunner().invoke(main, ["reflect", "hypothesis"])
    assert res.exit_code != 0, (
        f"empty-draft Phase 1 must abort Phase 2 (output={res.output!r})"
    )

    # Phase 1 ran; Phase 2 did NOT.
    assert len(mock_runners["sub_calls"]) == 1
    assert mock_runners["wrap_calls"] == []
    # Rich-console may word-wrap; normalize whitespace before substring check.
    flat_output = " ".join(res.output.split())
    assert "empty draft" in flat_output
    assert "markers missing" in flat_output
    assert "Phase 2 aborted" in flat_output


# --- observable UX ---------------------------------------------------------


def test_dry_run_observable(project_dir: Path, mock_runners):
    res = CliRunner().invoke(
        main, ["reflect", "hypothesis", "--dry-run"]
    )
    assert res.exit_code == 0
    assert "Handoff written" in res.output
    assert "Phase 1" not in res.output  # no exec
