"""Tests for `ai-hats reflect all` pre-flight + `reflect commit`."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.reflect import reflect
from ai_hats.paths import hypotheses_dir, proposals_dir, retros_dir
from ai_hats_rack.migration import migrate_catalog
from ai_hats_tracker.hypothesis import ProposalStore


def _seed(pd: Path) -> None:
    """Seed both catalogs' backlog.yaml so the workspace mounts them (R6)."""
    migrate_catalog(hypotheses_dir(pd), "hypotheses")
    migrate_catalog(proposals_dir(pd), "proposals")


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (hypotheses_dir(pd)).mkdir(parents=True)
    (proposals_dir(pd)).mkdir(parents=True)
    _seed(pd)
    monkeypatch.chdir(pd)
    return pd


def _make_hyp(pd: Path, hyp_id: str, status="active"):
    body = {
        "id": hyp_id, "title": f"hyp-{hyp_id}",
        "status": status, "created": "2026-01-01",
        "source_task": "HATS-001", "hypothesis": "h",
        "validation_log": [],
        "success_criterion": "x",
        "observation_window": "5 sessions",
    }
    (hypotheses_dir(pd) / f"{hyp_id}.yaml").write_text(
        yaml.safe_dump(body)
    )
    migrate_catalog(hypotheses_dir(pd), "hypotheses")  # flat → dir-per-card


def _make_prop(pd: Path, pid: str, status="open"):
    body = {
        "id": pid,
        "created": datetime(2026, 5, 4, tzinfo=timezone.utc).isoformat(),
        "title": f"title-{pid}", "category": "rule", "target": "x",
        "description": "d", "rationale": "r",
        "votes": [], "status": status,
    }
    (proposals_dir(pd) / f"{pid}.yaml").write_text(
        yaml.safe_dump(body)
    )
    migrate_catalog(proposals_dir(pd), "proposals")  # flat → dir-per-card


def test_dry_run_builds_handoff(project_dir: Path):
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")
    res = CliRunner().invoke(reflect, ["all", "--dry-run"])
    assert res.exit_code == 0, res.output
    out_dir = retros_dir(project_dir) / "reflect-all"
    files = list(out_dir.glob("*-handoff.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "HYP-001" in text
    assert "PROP-001" in text


def _make_hyp_with_protocol(pd: Path, hyp_id: str, protocol: str):
    """HATS-534 — make a HYP carrying verification_protocol via extra='allow'."""
    body = {
        "id": hyp_id, "title": f"hyp-{hyp_id}",
        "status": "active", "created": "2026-05-26",
        "source_task": "HATS-001", "hypothesis": "h",
        "validation_log": [],
        "success_criterion": "x",
        "observation_window": "5 sessions",
        "verification_protocol": protocol,
    }
    (hypotheses_dir(pd) / f"{hyp_id}.yaml").write_text(yaml.safe_dump(body))
    migrate_catalog(hypotheses_dir(pd), "hypotheses")  # flat → dir-per-card


def test_dry_run_handoff_surfaces_verification_protocol(project_dir: Path):
    """HATS-534 — verification_protocol on a HYP must render into the
    `reflect all` judge handoff so the auditor can follow Step 1.5."""
    protocol = (
        "STRICT — auditor evidence MUST be exactly three lines:\n"
        "Line 1: CRITERION: <verbatim>"
    )
    _make_hyp_with_protocol(project_dir, "HYP-501", protocol)
    res = CliRunner().invoke(reflect, ["all", "--dry-run"])
    assert res.exit_code == 0, res.output
    text = list(
        (retros_dir(project_dir) / "reflect-all").glob("*-handoff.md")
    )[0].read_text()
    assert "verification_protocol: |" in text, (
        "judge handoff missing verification_protocol literal-block header"
    )
    assert (
        "    STRICT — auditor evidence MUST be exactly three lines:" in text
    )
    assert "    Line 1: CRITERION: <verbatim>" in text


def test_dry_run_handoff_omits_verification_protocol_when_absent(
    project_dir: Path,
):
    """HATS-534 — HYPs without verification_protocol must not gain a stray
    label (no `verification_protocol: None` leftovers)."""
    _make_hyp(project_dir, "HYP-001")
    res = CliRunner().invoke(reflect, ["all", "--dry-run"])
    assert res.exit_code == 0
    text = list(
        (retros_dir(project_dir) / "reflect-all").glob("*-handoff.md")
    )[0].read_text()
    assert "HYP-001" in text
    assert "verification_protocol" not in text


def test_dry_run_handles_empty_inbox(project_dir: Path):
    res = CliRunner().invoke(reflect, ["all", "--dry-run"])
    assert res.exit_code == 0
    out_dir = retros_dir(project_dir) / "reflect-all"
    text = list(out_dir.glob("*-handoff.md"))[0].read_text()
    assert "no active hypotheses" in text
    assert "inbox empty" in text


def test_commit_changes_status(project_dir: Path):
    _make_prop(project_dir, "PROP-001")
    _make_prop(project_dir, "PROP-002")
    _make_prop(project_dir, "PROP-003")
    res = CliRunner().invoke(
        reflect,
        [
            "commit",
            "--accept", "PROP-001",
            "--reject", "PROP-002",
            "--defer", "PROP-003",
        ],
    )
    assert res.exit_code == 0, res.output

    store = ProposalStore(proposals_dir(project_dir))  # reads dir-per-card via shim
    assert store.load("PROP-001").status == "accepted"
    assert store.load("PROP-002").status == "rejected"
    assert store.load("PROP-003").status == "deferred"


def test_commit_with_no_changes(project_dir: Path):
    res = CliRunner().invoke(reflect, ["commit"])
    assert res.exit_code == 0
    assert "0 change(s)" in res.output
