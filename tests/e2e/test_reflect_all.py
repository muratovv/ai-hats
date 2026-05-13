"""E2E baseline for `ai-hats reflect all` — regression catcher for HATS-269."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import retros_dir



def _make_hyp(pd: Path, hyp_id: str):
    body = {
        "id": hyp_id, "title": f"hyp-{hyp_id}",
        "status": "active", "created": "2026-01-01",
        "source_task": "HATS-001", "hypothesis": "h",
        "validation_log": [],
        "success_criterion": "x",
        "observation_window": "5 sessions",
    }
    (pd / ".agent" / "hypotheses" / f"{hyp_id}.yaml").write_text(
        yaml.safe_dump(body)
    )


def _make_prop(pd: Path, pid: str):
    body = {
        "id": pid,
        "created": datetime(2026, 5, 4, tzinfo=timezone.utc).isoformat(),
        "title": f"title-{pid}", "category": "rule", "target": "x",
        "description": "d", "rationale": "r",
        "votes": [], "status": "open",
    }
    (pd / ".agent" / "backlog" / "proposals" / f"{pid}.yaml").write_text(
        yaml.safe_dump(body)
    )


def test_reflect_all_dry_run_writes_handoff_no_pipeline(
    project_dir: Path, mock_runners,
):
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    res = CliRunner().invoke(main, ["reflect", "all", "--dry-run"])
    assert res.exit_code == 0, res.output

    # Handoff written
    handoff_files = list(
        (retros_dir(project_dir) / "reflect-all").glob(
            "*-handoff.md"
        )
    )
    assert len(handoff_files) == 1, "handoff file expected after dry-run"

    # Pipeline NOT launched
    assert mock_runners["wrap_calls"] == []
    assert mock_runners["sub_calls"] == []


def test_reflect_all_full_routes_to_judge(
    project_dir: Path, mock_runners,
):
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")

    res = CliRunner().invoke(main, ["reflect", "all"])
    assert res.exit_code == 0, res.output

    # Handoff + judge launch
    handoff_files = list(
        (retros_dir(project_dir) / "reflect-all").glob(
            "*-handoff.md"
        )
    )
    assert len(handoff_files) >= 1

    assert len(mock_runners["wrap_calls"]) == 1
    call = mock_runners["wrap_calls"][0]
    assert call["role_override"] == "judge"
    # First positional in extra_args = combined preamble + handoff
    first_arg = call["extra_args"][0]
    assert "Reflect-all triage session" in first_arg
    assert "HYP-001" in first_arg
    assert "PROP-001" in first_arg


def test_reflect_all_observable_markers(
    project_dir: Path, mock_runners,
):
    """Observable: stdout has user-facing UX strings."""
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")
    res = CliRunner().invoke(main, ["reflect", "all"])
    assert res.exit_code == 0
    assert "Handoff written" in res.output
    assert "Launching judge" in res.output


def test_reflect_all_dry_run_observable(
    project_dir: Path, mock_runners,
):
    res = CliRunner().invoke(main, ["reflect", "all", "--dry-run"])
    assert res.exit_code == 0
    assert "Handoff written" in res.output
    assert "Launching judge" not in res.output
