"""E2E baseline for `ai-hats reflect issue` — full pipeline path.

Mocks SubAgentRunner so it writes a deterministic intake-result block to
the session's trace.log. Everything else runs for real: composer →
PipelineHarness → reflect-issue.yaml → compose_role → resolve_prompt →
launch_provider → extract_marker → parser → HypothesisStore.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from ai_hats.cli import main
from ai_hats.paths import hypotheses_dir


def _install_subagent_trace(monkeypatch, project_dir: Path, body: str) -> dict:
    """Replace SubAgentRunner so it emits ``body`` as the transcript content.

    Returns the captured-call dict so tests can assert on what was passed.
    """
    captured: dict = {"calls": []}

    class _Session:
        def __init__(self, sid: str = "intake-1") -> None:
            self.session_id = sid
            self.session_dir = (
                project_dir / ".gitlog" / f"session_{sid}"
            )
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.trace_path = self.session_dir / "trace.log"
            self.trace_path.write_text("(sub-agent system events only)\n")
            # _finalize_sub_agent writes LLM stdout to transcript.txt;
            # launch_provider exposes that file as transcript_path for
            # non-interactive runs.
            (self.session_dir / "transcript.txt").write_text(body)
            self.metrics_path = self.session_dir / "metrics.json"
            self.metrics_path.write_text(
                json.dumps({
                    "exit_code": 0,
                    "session_id": sid,
                    "role": "hypothesis-intake",
                    "duration_s": 0.1,
                })
            )

    class _Runner:
        def __init__(self, _pd) -> None:
            pass

        def run(self, **kwargs):
            captured["calls"].append(kwargs)
            return _Session()

    import ai_hats.runtime as rt

    monkeypatch.setattr(rt, "SubAgentRunner", _Runner)
    return captured


def _bootstrap_silenced(monkeypatch) -> None:
    import ai_hats._bootstrap as boot

    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)


def test_reflect_issue_create_full_pipeline(
    project_dir: Path, monkeypatch,
) -> None:
    """No active HYPs + create action → HYP-001 materializes."""
    _bootstrap_silenced(monkeypatch)
    trace = (
        "some preamble from the agent\n"
        "BEGIN_INTAKE_RESULT\n"
        "action: create\n"
        "draft:\n"
        "  title: agent skips parameterized SQL\n"
        "  hypothesis: agent uses f-strings in SQL queries\n"
        "  baseline: every audited call used f-strings\n"
        "  expected_outcome:\n"
        "    - audit catches 0 concat calls\n"
        "  success_criterion: 0 concat across 4 sessions\n"
        "  exit_criteria:\n"
        "    confirm: [4 sessions clean]\n"
        "    refute: [any concat after rule]\n"
        "    stalled: []\n"
        "END_INTAKE_RESULT\n"
        "trailing noise\n"
    )
    captured = _install_subagent_trace(monkeypatch, project_dir, trace)

    res = CliRunner().invoke(
        main,
        ["reflect", "issue", "agent uses f-strings in SQL"],
    )
    assert res.exit_code == 0, res.output
    assert "created HYP-001" in res.output

    # Pipeline actually invoked SubAgentRunner with our role + model
    assert len(captured["calls"]) == 1
    call = captured["calls"][0]
    assert call["role_name"] == "hypothesis-intake"
    assert call["model"] == "haiku"

    # HYP file on disk with parsed draft fields
    saved = yaml.safe_load(
        (hypotheses_dir(project_dir) / "HYP-001.yaml").read_text()
    )
    assert saved["status"] == "active"
    assert saved["source_task"] == "supervisor-observation"
    assert saved["title"].startswith("agent skips")
    assert saved["exit_criteria"]["confirm"] == ["4 sessions clean"]


def test_reflect_issue_merge_full_pipeline(
    project_dir: Path, monkeypatch,
) -> None:
    """Active HYP + merge action → validation_log appended, no new file."""
    _bootstrap_silenced(monkeypatch)
    # Seed one active HYP
    seed = {
        "id": "HYP-001",
        "title": "agent skips param SQL",
        "status": "active",
        "created": "2026-05-01",
        "source_task": "HATS-100",
        "hypothesis": "agent uses f-strings in SQL queries",
        "validation_log": [],
    }
    (hypotheses_dir(project_dir) / "HYP-001.yaml").write_text(
        yaml.safe_dump(seed)
    )

    trace = (
        "BEGIN_INTAKE_RESULT\n"
        "action: merge\n"
        "target_id: HYP-001\n"
        "evidence: same f-string SQL pattern resurfaced in pipeline.py\n"
        "END_INTAKE_RESULT\n"
    )
    _install_subagent_trace(monkeypatch, project_dir, trace)

    res = CliRunner().invoke(
        main,
        [
            "reflect", "issue", "saw it again in pipeline.py",
            "--session", "20260512-120000-1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "merged into HYP-001" in res.output

    files = list((hypotheses_dir(project_dir)).glob("HYP-*.yaml"))
    assert len(files) == 1
    saved = yaml.safe_load(files[0].read_text())
    assert len(saved["validation_log"]) == 1
    entry = saved["validation_log"][0]
    assert entry["verdict"] == "inconclusive"
    assert entry["evidence"].startswith("same f-string")
    assert entry["session_id"] == "20260512-120000-1"


def test_reflect_issue_missing_markers_with_active_hyp_fails(
    project_dir: Path, monkeypatch,
) -> None:
    """LLM produced output without the marker block — must fail-loud."""
    _bootstrap_silenced(monkeypatch)
    (hypotheses_dir(project_dir) / "HYP-001.yaml").write_text(
        yaml.safe_dump({
            "id": "HYP-001", "title": "t", "status": "active",
            "created": "2026-05-01", "source_task": "HATS-100",
            "hypothesis": "h", "validation_log": [],
        })
    )
    _install_subagent_trace(monkeypatch, project_dir, "no markers here\n")

    res = CliRunner().invoke(
        main, ["reflect", "issue", "obs"],
    )
    assert res.exit_code != 0
    # Either fail-loud about active hyps or about missing markers
    assert "active hypotheses exist" in res.output or "did not emit" in res.output
