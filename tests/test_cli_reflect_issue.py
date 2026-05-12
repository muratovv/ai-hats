"""CLI tests for `ai-hats reflect issue` — mocks pipeline at module boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.reflect import reflect


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (pd / ".agent" / "hypotheses").mkdir(parents=True)
    monkeypatch.chdir(pd)
    return pd


def _write_active_hyp(pd: Path, hyp_id: str, **extras) -> Path:
    body = {
        "id": hyp_id,
        "title": extras.pop("title", f"title-{hyp_id}"),
        "status": "active",
        "created": "2026-01-01",
        "source_task": "HATS-001",
        "hypothesis": extras.pop("hypothesis", "h"),
        "validation_log": [],
    }
    body.update(extras)
    p = pd / ".agent" / "hypotheses" / f"{hyp_id}.yaml"
    p.write_text(yaml.safe_dump(body))
    return p


def _mock_pipeline(monkeypatch, *, result_text: str, exit_code: int = 0):
    """Patch _run_intake_pipeline to return the given marker block content."""
    import ai_hats.cli.reflect as mod

    def fake(project_dir, prompt_text):
        return result_text, exit_code

    monkeypatch.setattr(mod, "_run_intake_pipeline", fake)


def _mock_pipeline_raises(monkeypatch, exc: Exception):
    import ai_hats.cli.reflect as mod

    def fake(project_dir, prompt_text):
        raise exc

    monkeypatch.setattr(mod, "_run_intake_pipeline", fake)


def test_create_writes_hyp_file(project_dir, monkeypatch):
    _mock_pipeline(
        monkeypatch,
        result_text=(
            "action: create\n"
            "draft:\n"
            "  title: agent ignores parameterized SQL\n"
            "  hypothesis: agent uses f-strings in SQL\n"
            "  baseline: every audited call used f-strings\n"
            "  expected_outcome:\n"
            "    - audit catches 0 concat calls\n"
            "  success_criterion: 0 concat calls across 4 sessions\n"
            "  exit_criteria:\n"
            "    confirm: [4 sessions clean]\n"
            "    refute: [any concat after rule]\n"
            "    stalled: []\n"
        ),
    )
    res = CliRunner().invoke(reflect, ["issue", "agent skips param queries", "--confirm"])
    assert res.exit_code == 0, res.output
    assert "created HYP-001" in res.output
    saved = yaml.safe_load(
        (project_dir / ".agent" / "hypotheses" / "HYP-001.yaml").read_text()
    )
    assert saved["status"] == "active"
    assert saved["source_task"] == "supervisor-observation"
    assert saved["title"].startswith("agent ignores")
    assert saved["exit_criteria"]["confirm"] == ["4 sessions clean"]


def test_merge_appends_validation_log_no_new_file(project_dir, monkeypatch):
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline(
        monkeypatch,
        result_text=(
            "action: merge\n"
            "target_id: HYP-001\n"
            "evidence: same pattern seen again in pipeline.py review\n"
        ),
    )
    res = CliRunner().invoke(
        reflect,
        [
            "issue",
            "saw it again in pipeline.py",
            "--confirm",
            "--session",
            "20260512-120000-1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "merged into HYP-001" in res.output
    # Still exactly one HYP file
    files = list((project_dir / ".agent" / "hypotheses").glob("HYP-*.yaml"))
    assert len(files) == 1
    saved = yaml.safe_load(files[0].read_text())
    assert len(saved["validation_log"]) == 1
    entry = saved["validation_log"][0]
    assert entry["verdict"] == "inconclusive"
    assert entry["evidence"].startswith("same pattern")
    assert entry["session_id"] == "20260512-120000-1"


def test_pipeline_failure_with_active_hyps_fails_loud(project_dir, monkeypatch):
    """When dedup is needed but the LLM failed — refuse to write."""
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline_raises(monkeypatch, RuntimeError("simulated network"))
    res = CliRunner().invoke(
        reflect, ["issue", "some observation", "--confirm"]
    )
    assert res.exit_code != 0
    assert "active hypotheses exist" in res.output
    # Original HYP intact, no new files
    files = list((project_dir / ".agent" / "hypotheses").glob("HYP-*.yaml"))
    assert len(files) == 1


def test_pipeline_failure_no_active_hyps_graceful_degrade(project_dir, monkeypatch):
    """No active HYPs + LLM failed → minimal HYP created."""
    _mock_pipeline_raises(monkeypatch, RuntimeError("api down"))
    res = CliRunner().invoke(
        reflect, ["issue", "an observation worth keeping", "--confirm"]
    )
    assert res.exit_code == 0, res.output
    assert "degraded" in res.output
    saved = yaml.safe_load(
        (project_dir / ".agent" / "hypotheses" / "HYP-001.yaml").read_text()
    )
    assert saved["hypothesis"] == "an observation worth keeping"
    # Title is truncated to <=60 chars; baseline/exit_criteria left null/empty
    assert len(saved["title"]) <= 60
    assert saved.get("baseline") is None
    assert saved["expected_outcome"] == []


def test_empty_marker_block_triggers_fail_loud(project_dir, monkeypatch):
    """Marker missing → pipeline produced empty intake_result; with active HYP must fail-loud."""
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline(monkeypatch, result_text="", exit_code=0)
    res = CliRunner().invoke(reflect, ["issue", "x", "--confirm"])
    assert res.exit_code != 0
    assert "did not emit" in res.output or "BEGIN_INTAKE_RESULT" in res.output


def test_non_confirm_aborts(project_dir, monkeypatch):
    _mock_pipeline(
        monkeypatch,
        result_text=(
            "action: create\n"
            "draft:\n"
            "  title: t\n"
            "  hypothesis: h\n"
        ),
    )
    # No --confirm; CliRunner's stdin defaults to nothing → confirm() returns default=False
    res = CliRunner().invoke(reflect, ["issue", "x"], input="n\n")
    assert res.exit_code == 0
    assert "aborted" in res.output
    files = list((project_dir / ".agent" / "hypotheses").glob("HYP-*.yaml"))
    assert files == []


def test_merge_unknown_target_fails_loud(project_dir, monkeypatch):
    """LLM hallucinated a HYP id that doesn't exist → refuse to fabricate."""
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline(
        monkeypatch,
        result_text=(
            "action: merge\n"
            "target_id: HYP-999\n"
            "evidence: hallucinated\n"
        ),
    )
    res = CliRunner().invoke(reflect, ["issue", "x", "--confirm"])
    assert res.exit_code != 0
    assert "HYP-999" in res.output


def test_task_id_overrides_source_task(project_dir, monkeypatch):
    _mock_pipeline(
        monkeypatch,
        result_text=(
            "action: create\n"
            "draft:\n"
            "  title: t\n"
            "  hypothesis: h\n"
        ),
    )
    res = CliRunner().invoke(
        reflect, ["issue", "obs", "--confirm", "--task", "HATS-304"]
    )
    assert res.exit_code == 0, res.output
    saved = yaml.safe_load(
        (project_dir / ".agent" / "hypotheses" / "HYP-001.yaml").read_text()
    )
    assert saved["source_task"] == "HATS-304"
