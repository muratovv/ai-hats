"""Tests for `ai-hats task hyp` CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.hyp import hyp
from ai_hats.paths import hypotheses_dir, proposals_dir


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (hypotheses_dir(pd)).mkdir(parents=True)
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
    p = hypotheses_dir(pd) / f"{hyp_id}.yaml"
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


def test_create_auto_id(project_dir: Path):
    res = _invoke(
        [
            "create",
            "--title",
            "smoke",
            "--hypothesis",
            "x improves y",
            "--source-task",
            "HATS-001",
        ]
    )
    assert res.exit_code == 0, res.output
    assert "HYP-001" in res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    assert p.exists()
    data = yaml.safe_load(p.read_text())
    assert data["id"] == "HYP-001"
    assert data["status"] == "active"
    assert data["title"] == "smoke"
    assert data["hypothesis"] == "x improves y"
    assert data["source_task"] == "HATS-001"


def test_create_increments_id(project_dir: Path):
    _invoke(
        [
            "create",
            "--title",
            "first",
            "--hypothesis",
            "h1",
            "--source-task",
            "HATS-001",
        ]
    )
    res = _invoke(
        [
            "create",
            "--title",
            "second",
            "--hypothesis",
            "h2",
            "--source-task",
            "HATS-002",
        ]
    )
    assert res.exit_code == 0
    assert "HYP-002" in res.output


def test_create_duplicate_id_rejected(project_dir: Path, monkeypatch):
    """A duplicate HYP id must exit 1 with a clean error, not a traceback.

    next_hypothesis_id auto-bumps past collisions, so to exercise the
    FileExistsError path we stub it to return an id that already exists.
    """
    _write_hyp(project_dir, "HYP-001", title="original")
    monkeypatch.setattr("ai_hats.cli.hyp.next_hypothesis_id", lambda _d: "HYP-001")
    res = _invoke(
        [
            "create",
            "--title",
            "duplicate",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
        ]
    )
    assert res.exit_code == 1, res.output
    assert "already exists" in res.output.lower()
    assert "Traceback" not in res.output
    data = yaml.safe_load((hypotheses_dir(project_dir) / "HYP-001.yaml").read_text())
    assert data["title"] == "original"


def test_create_with_optional_fields(project_dir: Path):
    res = _invoke(
        [
            "create",
            "--title",
            "full",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
            "--baseline",
            "current state X",
            "--expected-outcome",
            "metric A drops",
            "--expected-outcome",
            "metric B stable",
            "--observation-window",
            "4 sessions",
            "--success-criterion",
            "A < threshold",
        ]
    )
    assert res.exit_code == 0
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    data = yaml.safe_load(p.read_text())
    assert data["expected_outcome"] == ["metric A drops", "metric B stable"]
    assert data["observation_window"] == "4 sessions"


def test_create_with_verification_protocol(project_dir: Path):
    """HATS-623: --verification-protocol round-trips into the YAML (extra field)."""
    res = _invoke(
        [
            "create",
            "--title",
            "lib-change",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
            "--verification-protocol",
            "Run suite X, observe metric Y unchanged",
        ]
    )
    assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    data = yaml.safe_load(p.read_text())
    assert data["verification_protocol"] == "Run suite X, observe metric Y unchanged"


def test_create_without_verification_protocol_omits_key(project_dir: Path):
    """HATS-623: absent flag → key not written (exclude_none, no magic empty)."""
    res = _invoke(
        [
            "create",
            "--title",
            "no-vp",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
        ]
    )
    assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    data = yaml.safe_load(p.read_text())
    assert "verification_protocol" not in data


def test_create_verification_protocol_multiline_roundtrip(project_dir: Path):
    """HATS-623: a multi-line protocol survives the YAML round-trip verbatim
    (the reflect.py consumer renders it as a literal block scalar)."""
    vp = "Step 1: run suite X\nStep 2: observe metric Y unchanged"
    res = _invoke(
        [
            "create",
            "--title",
            "multiline",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
            "--verification-protocol",
            vp,
        ]
    )
    assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    assert yaml.safe_load(p.read_text())["verification_protocol"] == vp


def test_create_verification_protocol_empty_string_is_written(project_dir: Path):
    """HATS-623: an explicit empty value writes an empty string (consistent
    with the sibling optionals' is-not-None semantics) — pins the behaviour."""
    res = _invoke(
        [
            "create",
            "--title",
            "empty-vp",
            "--hypothesis",
            "h",
            "--source-task",
            "HATS-001",
            "--verification-protocol",
            "",
        ]
    )
    assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    assert yaml.safe_load(p.read_text())["verification_protocol"] == ""


def test_set_status_flips(project_dir: Path):
    _write_hyp(project_dir, "HYP-001", status="active")
    res = _invoke(["set-status", "--hyp", "HYP-001", "--status", "confirmed"])
    assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    assert yaml.safe_load(p.read_text())["status"] == "confirmed"


def test_set_status_unknown_fails(project_dir: Path):
    res = _invoke(["set-status", "--hyp", "HYP-999", "--status", "stalled"])
    assert res.exit_code != 0
    assert "not found" in res.output


def test_set_status_round_trip(project_dir: Path):
    _write_hyp(project_dir, "HYP-001", status="active")
    for s in ["confirmed", "active", "refuted", "stalled"]:
        res = _invoke(["set-status", "--hyp", "HYP-001", "--status", s])
        assert res.exit_code == 0, res.output
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    assert yaml.safe_load(p.read_text())["status"] == "stalled"


def test_append_verdict_adds_entry(project_dir: Path):
    p = _write_hyp(project_dir, "HYP-001")
    res = _invoke(
        [
            "append-verdict",
            "--hyp",
            "HYP-001",
            "--session",
            "s1",
            "--verdict",
            "confirmed",
            "--evidence",
            "metric drop observed",
            "--recommendation",
            "close_confirmed",
        ]
    )
    assert res.exit_code == 0, res.output
    data = yaml.safe_load(p.read_text())
    assert len(data["validation_log"]) == 1
    e = data["validation_log"][0]
    assert e["verdict"] == "confirmed"
    assert e["session_id"] == "s1"


def test_append_verdict_unknown_hyp_fails(project_dir: Path):
    res = _invoke(
        [
            "append-verdict",
            "--hyp",
            "HYP-999",
            "--session",
            "s1",
            "--verdict",
            "confirmed",
            "--evidence",
            "x",
        ]
    )
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
    p = hypotheses_dir(project_dir) / "HYP-001.yaml"
    p.write_text(yaml.safe_dump(body))

    res1 = _invoke(["migrate"])
    assert res1.exit_code == 0, res1.output
    after1 = yaml.safe_load(p.read_text())
    assert after1["min_sessions_per_bundle"] == 4
    assert "exit_criteria" in after1
    assert after1["validation_log"][0]["verdict"] == "refuted"
    assert after1["validation_log"][0]["evidence"] == "/tmp/foo.md"
    assert after1["legacy_field"] == "preserve"
    assert (proposals_dir(project_dir)).exists()

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


# ---- autoclose (HATS-769) ----


def _refuted(session_id: str) -> dict:
    return {
        "date": "2026-06-10",
        "verdict": "refuted",
        "evidence": "behaviour gone",
        "session_id": session_id,
    }


def test_autoclose_flips_status_on_quorum(project_dir: Path):
    p = _write_hyp(
        project_dir, "HYP-001", validation_log=[_refuted("s1"), _refuted("s2"), _refuted("s3")]
    )
    res = _invoke(["autoclose"])
    assert res.exit_code == 0, res.output
    assert "HYP-001" in res.output
    data = yaml.safe_load(p.read_text())
    assert data["status"] == "refuted"
    # Closure is logged with the contributing sessions named (audit trail).
    audit = data["validation_log"][-1]
    assert audit["session_id"] == "auto-quorum"
    assert "s1, s2, s3" in audit["evidence"]


def test_autoclose_below_quorum_leaves_active(project_dir: Path):
    p = _write_hyp(project_dir, "HYP-001", validation_log=[_refuted("s1"), _refuted("s2")])
    res = _invoke(["autoclose"])
    assert res.exit_code == 0, res.output
    assert "closed: none" in res.output
    assert yaml.safe_load(p.read_text())["status"] == "active"


def test_autoclose_dry_run_does_not_mutate(project_dir: Path):
    p = _write_hyp(
        project_dir, "HYP-001", validation_log=[_refuted("s1"), _refuted("s2"), _refuted("s3")]
    )
    res = _invoke(["autoclose", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "HYP-001" in res.output
    data = yaml.safe_load(p.read_text())
    assert data["status"] == "active"
    assert all(e["session_id"] != "auto-quorum" for e in data["validation_log"])


def test_autoclose_custom_k(project_dir: Path):
    p = _write_hyp(project_dir, "HYP-001", validation_log=[_refuted("s1"), _refuted("s2")])
    res = _invoke(["autoclose", "--k", "2"])
    assert res.exit_code == 0, res.output
    assert yaml.safe_load(p.read_text())["status"] == "refuted"


def test_autoclose_rejects_k_below_one(project_dir: Path):
    res = _invoke(["autoclose", "--k", "0"])
    assert res.exit_code != 0
    assert ">= 1" in res.output
