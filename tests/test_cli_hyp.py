"""Tests for `ai-hats hyp` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.hyp import hyp


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (pd / ".agent" / "hypotheses").mkdir(parents=True)
    monkeypatch.chdir(pd)
    return pd


def _write_hyp(pd: Path, hyp_id: str, **extras) -> Path:
    body = {
        "id": hyp_id,
        "title": f"title-{hyp_id}",
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [],
    }
    body.update(extras)
    p = pd / ".agent" / "hypotheses" / f"{hyp_id}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def _invoke(args):
    return CliRunner().invoke(hyp, args)


def test_list_filters_by_status(project_dir: Path):
    _write_hyp(project_dir, "HYP-001", status="active")
    _write_hyp(project_dir, "HYP-002", status="confirmed")
    res = _invoke(["list", "--status", "active", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert [d["id"] for d in data] == ["HYP-001"]


def test_show_prints_yaml(project_dir: Path):
    _write_hyp(project_dir, "HYP-001")
    res = _invoke(["show", "HYP-001"])
    assert res.exit_code == 0
    assert "HYP-001" in res.output


def test_show_unknown_fails(project_dir: Path):
    res = _invoke(["show", "HYP-999"])
    assert res.exit_code != 0
    assert "not found" in res.output


def test_append_verdict_adds_entry(project_dir: Path):
    p = _write_hyp(project_dir, "HYP-001")
    res = _invoke([
        "append-verdict",
        "--hyp", "HYP-001",
        "--session", "s1",
        "--verdict", "confirmed",
        "--evidence", "metric drop observed",
        "--recommendation", "close_confirmed",
    ])
    assert res.exit_code == 0, res.output
    data = yaml.safe_load(p.read_text())
    assert len(data["validation_log"]) == 1
    e = data["validation_log"][0]
    assert e["verdict"] == "confirmed"
    assert e["session_id"] == "s1"


def test_append_verdict_unknown_hyp_fails(project_dir: Path):
    res = _invoke([
        "append-verdict",
        "--hyp", "HYP-999",
        "--session", "s1",
        "--verdict", "confirmed",
        "--evidence", "x",
    ])
    assert res.exit_code != 0


def test_migrate_idempotent(project_dir: Path):
    body = {
        "id": "HYP-001",
        "title": "t",
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": "h",
        "validation_log": [
            {
                "date": "2026-04-10",
                "bundle": "BUNDLE-2026-04-10-001",
                "sweep_report": "/tmp/foo.md",
                "verdict": "refuted",
            }
        ],
        "legacy_field": "preserve",
    }
    p = project_dir / ".agent" / "hypotheses" / "HYP-001.yaml"
    p.write_text(yaml.safe_dump(body))

    res1 = _invoke(["migrate"])
    assert res1.exit_code == 0, res1.output
    after1 = yaml.safe_load(p.read_text())
    assert after1["min_sessions_per_bundle"] == 4
    assert "exit_criteria" in after1
    assert after1["validation_log"][0]["verdict"] == "refuted"
    assert after1["validation_log"][0]["evidence"] == "/tmp/foo.md"
    assert after1["legacy_field"] == "preserve"
    assert (project_dir / ".agent" / "backlog" / "proposals").exists()

    res2 = _invoke(["migrate"])
    assert res2.exit_code == 0
    after2 = yaml.safe_load(p.read_text())
    assert after1 == after2
    assert "0 file(s) changed" in res2.output


def test_migrate_dry_run_does_not_write(project_dir: Path):
    p = _write_hyp(project_dir, "HYP-001")
    before = p.read_text()
    res = _invoke(["migrate", "--dry-run"])
    assert res.exit_code == 0
    after = p.read_text()
    assert before == after
