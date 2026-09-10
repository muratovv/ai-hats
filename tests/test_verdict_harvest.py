"""Verdict-harvest tests (HATS-1369).

``_harvest_verdicts`` persists non-``n/a`` ``SessionReviewV1`` verdicts into
the HYP's ``validation_log`` via ``rack_workspace.append_verdict`` — closing
the gap where ``session-reviewer`` wrote a verdict to disk but nobody pushed
it into the HYP card. The bottom test chains harvest -> ``quorum_autoclose``
(unmodified — the sum-over-independent-sessions math was already correct).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

from ai_hats.cli.reflect_session_main import _harvest_verdicts
from ai_hats.rack_workspace import autoclose_hypotheses, rack_workspace
from ai_hats_rack.extensions.quorum import AUTOCLOSE_ACTOR
from ai_hats_rack.migration import migrate_catalog


def _add_active_hyp(project_dir: Path, hyp_id: str = "HYP-001") -> None:
    hyps_dir = ProjectLayout.at(project_dir).tracker.base / "backlog" / "hypotheses"
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
    migrate_catalog(hyps_dir, "hypotheses")  # flat -> dir-per-card for the workspace


def _validation_log(project_dir: Path, hyp_id: str) -> list[dict]:
    ws = rack_workspace(ProjectLayout.at(project_dir))
    card = ws.kernel_for(hyp_id).get(hyp_id)
    assert card is not None, f"{hyp_id} vanished"
    return list(card.extras.get("validation_log") or [])


# ---- unit: field-level persistence contract ----


def test_harvest_persists_non_na_verdict_with_correct_fields(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-001")
    verdicts = [
        {
            "hyp_id": "HYP-001",
            "verdict": "confirmed",
            "evidence": "observed X during the session",
            "recommendation": "close_confirmed",
        }
    ]

    persisted = _harvest_verdicts(ProjectLayout.at(tmp_path), "sess-001", verdicts)

    assert persisted == ["HYP-001"]
    [entry] = _validation_log(tmp_path, "HYP-001")
    assert entry["verdict"] == "confirmed"
    assert entry["evidence"] == "observed X during the session"
    assert entry["recommendation"] == "close_confirmed"
    assert entry["session_id"] == "sess-001"
    assert entry["date"] and entry["timestamp"]


def test_harvest_skips_na_verdict(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-001")
    verdicts = [{"hyp_id": "HYP-001", "verdict": "n/a", "evidence": "not relevant this session"}]

    persisted = _harvest_verdicts(ProjectLayout.at(tmp_path), "sess-001", verdicts)

    assert persisted == []
    assert _validation_log(tmp_path, "HYP-001") == []


def test_harvest_skips_verdict_missing_hyp_id_without_crashing(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-001")
    verdicts = [{"verdict": "confirmed", "evidence": "e"}]  # no hyp_id

    persisted = _harvest_verdicts(ProjectLayout.at(tmp_path), "sess-001", verdicts)

    assert persisted == []
    assert _validation_log(tmp_path, "HYP-001") == []


def test_harvest_session_id_is_the_observed_session_not_the_verdict_payload(
    tmp_path: Path,
) -> None:
    """``session_id`` must be the function's own argument (the OBSERVED
    session) — a stray ``session_id`` key inside the verdict payload (e.g. a
    judge/session-reviewer's own id) must never leak into the entry."""
    _add_active_hyp(tmp_path, "HYP-001")
    verdicts = [
        {
            "hyp_id": "HYP-001",
            "verdict": "refuted",
            "evidence": "e",
            "recommendation": "close_refuted",
            "session_id": "some-other-judge-session",
        }
    ]

    _harvest_verdicts(ProjectLayout.at(tmp_path), "observed-session-42", verdicts)

    [entry] = _validation_log(tmp_path, "HYP-001")
    assert entry["session_id"] == "observed-session-42"


def test_harvest_one_failure_does_not_abort_the_rest(tmp_path: Path) -> None:
    _add_active_hyp(tmp_path, "HYP-001")
    verdicts = [
        {"hyp_id": "HYP-999", "verdict": "confirmed", "evidence": "unknown hyp"},
        {"hyp_id": "HYP-001", "verdict": "confirmed", "evidence": "known hyp"},
    ]

    persisted = _harvest_verdicts(ProjectLayout.at(tmp_path), "sess-001", verdicts)

    assert persisted == ["HYP-001"]
    [entry] = _validation_log(tmp_path, "HYP-001")
    assert entry["evidence"] == "known hyp"


# ---- integration: harvest -> quorum_autoclose (closes the research gap) ----


def test_three_independent_harvested_refuted_verdicts_reach_quorum_autoclose(
    tmp_path: Path,
) -> None:
    """Three separate ``reflect_session_main`` runs (one per real session) each
    harvest a ``refuted`` verdict; the existing (unmodified) quorum-autoclose
    mechanism must then close the HYP — proving the persist gap, not the
    quorum math, was the bug."""
    _add_active_hyp(tmp_path, "HYP-001")
    for i, sid in enumerate(("sess-a", "sess-b", "sess-c"), start=1):
        verdicts = [
            {
                "hyp_id": "HYP-001",
                "verdict": "refuted",
                "evidence": f"independent session {i} found no supporting evidence",
                "recommendation": "close_refuted",
            }
        ]
        persisted = _harvest_verdicts(ProjectLayout.at(tmp_path), sid, verdicts)
        assert persisted == ["HYP-001"]

    assert len(_validation_log(tmp_path, "HYP-001")) == 3

    ws = rack_workspace(ProjectLayout.at(tmp_path))
    closures = autoclose_hypotheses(ws, caller_cwd=tmp_path, k=3, actor=AUTOCLOSE_ACTOR)

    assert [c.hyp_id for c in closures] == ["HYP-001"]
    card = ws.kernel_for("HYP-001").get("HYP-001")
    assert card is not None
    assert card.state == "refuted"
