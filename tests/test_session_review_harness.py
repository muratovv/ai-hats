"""Harness-check tests for session-reviewer (HATS-252).

The harness is the single owner of the failure-proposal: when the persisted
review artifact is missing, malformed, or incomplete, exactly one meta-proposal
is filed under .agent/backlog/proposals/.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ai_hats.cli.reflect_session_main import (
    _file_meta_proposal,
    _harness_check,
)
from ai_hats_rack.migrate import migrate_catalog
from ai_hats_tracker.hypothesis import ProposalStore
from ai_hats.paths import hypotheses_dir, proposals_dir, retros_dir


SID = "20260506-100000-1"


def _seed(project_dir: Path) -> None:
    """Seed both HYP/PROP catalogs with backlog.yaml so the workspace mounts them
    (HATS-1044 R6: consumers require the migrated dir-per-card layout)."""
    migrate_catalog(hypotheses_dir(project_dir), "hypotheses")
    migrate_catalog(proposals_dir(project_dir), "proposals")


def _make_review_file(
    project_dir: Path, *, summary: str = "ok", verdicts=None,
) -> Path:
    out = retros_dir(project_dir) / "sessions" / f"{SID}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "schema": "hats-session-review/v1",
        "session_id": SID,
        "summary": summary,
        "hypothesis_verdicts": verdicts or [],
    }
    out.write_text(f"---\n{yaml.safe_dump(fm)}---\n\n# body\n")
    return out


def _add_active_hyp(project_dir: Path, hyp_id: str = "HYP-001") -> None:
    hyps_dir = hypotheses_dir(project_dir)
    hyps_dir.mkdir(parents=True, exist_ok=True)
    (hyps_dir / f"{hyp_id}.yaml").write_text(
        "id: " + hyp_id + "\n"
        "title: t\n"
        "status: active\n"
        "created: '2026-05-01'\n"
        "source_task: TASK-001\n"
        "hypothesis: a\n"
        "validation_log: []\n"
    )
    migrate_catalog(hyps_dir, "hypotheses")  # flat → dir-per-card for the workspace


def _proposals_count(project_dir: Path) -> int:
    pdir = proposals_dir(project_dir)
    if not pdir.exists():
        return 0
    return len(list(pdir.glob("*/task.yaml")))  # dir-per-card cards


# ---- harness_check return value ----


def test_harness_passes_on_valid_output(tmp_path: Path) -> None:
    _make_review_file(tmp_path, summary="x")
    issues = _harness_check(tmp_path, SID, runner_error=None)
    assert issues == []


def test_harness_flags_missing_file(tmp_path: Path) -> None:
    issues = _harness_check(tmp_path, SID, runner_error=None)
    assert any("missing" in i or "empty" in i for i in issues)


def test_harness_flags_empty_summary(tmp_path: Path) -> None:
    _make_review_file(tmp_path, summary="")
    issues = _harness_check(tmp_path, SID, runner_error=None)
    assert any("summary" in i for i in issues)


def test_harness_flags_missing_active_hyp_verdict(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-007")
    _make_review_file(tmp_path, summary="ok", verdicts=[])
    issues = _harness_check(tmp_path, SID, runner_error=None)
    assert any("HYP-007" in i for i in issues)


def test_harness_passes_when_no_active_hyps_and_empty_verdicts(tmp_path: Path) -> None:
    _make_review_file(tmp_path, summary="ok", verdicts=[])
    issues = _harness_check(tmp_path, SID, runner_error=None)
    assert issues == []


def test_harness_surfaces_runner_error_even_with_valid_file(tmp_path: Path) -> None:
    _make_review_file(tmp_path, summary="ok")
    issues = _harness_check(tmp_path, SID, runner_error="LLM TimeoutError")
    assert any("runner reported" in i for i in issues)


# ---- file_meta_proposal: single ownership + de-dup ----


def test_file_meta_proposal_creates_one(tmp_path: Path) -> None:
    _seed(tmp_path)
    _file_meta_proposal(tmp_path, SID, ["output file missing or empty"])
    assert _proposals_count(tmp_path) == 1
    store = ProposalStore(proposals_dir(tmp_path))
    [prop] = store.list_all()
    assert prop.category == "process"
    assert prop.target == "session-reviewer"
    assert prop.failed_session_id == SID


def test_file_meta_proposal_deduplicates_by_failed_session(tmp_path: Path) -> None:
    """Second call for the same session must NOT create a second proposal."""
    _seed(tmp_path)
    _file_meta_proposal(tmp_path, SID, ["issue 1"])
    _file_meta_proposal(tmp_path, SID, ["issue 2"])
    assert _proposals_count(tmp_path) == 1


def test_file_meta_proposal_distinct_sessions_distinct_proposals(tmp_path: Path) -> None:
    _seed(tmp_path)
    _file_meta_proposal(tmp_path, "SID-A", ["a"])
    _file_meta_proposal(tmp_path, "SID-B", ["b"])
    assert _proposals_count(tmp_path) == 2
