"""CLI tests for `ai-hats reflect issue` — mocks pipeline at module boundary.

HATS-1044 R6: the command writes/reads the HYP backlog through the rack
workspace (dir-per-card). Fixtures seed the catalog with its ``backlog.yaml`` and
migrate any flat HYP via the migration tool (good dogfood); assertions read back
through the rack itself (HATS-1264) — the consumer-facing ``HypView`` for what
``reflect``/``judge`` render, the stored card for the fields that view does not
carry (source_task link, exit_criteria, baseline).
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli.reflect import reflect
from ai_hats.rack_workspace import HypView, active_hypotheses, rack_workspace
from ai_hats_rack.migration import migrate_catalog


def _migrate(pd: Path) -> None:
    """Seed the HYP catalog's backlog.yaml + migrate any flat file to dir-per-card."""
    migrate_catalog(ProjectLayout.at(pd).tracker.base / "backlog" / "hypotheses", "hypotheses")


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    (ProjectLayout.at(pd).tracker.base / "backlog" / "hypotheses").mkdir(parents=True)
    _migrate(pd)  # seed backlog.yaml so the workspace mounts the HYP backlog
    monkeypatch.chdir(pd)
    return pd


def _write_active_hyp(pd: Path, hyp_id: str, **extras) -> None:
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
    (ProjectLayout.at(pd).tracker.base / "backlog" / "hypotheses" / f"{hyp_id}.yaml").write_text(
        yaml.safe_dump(body)
    )
    _migrate(pd)  # flat → dir-per-card so the workspace reads it


def _card(pd: Path, hyp_id: str):
    """The stored card, read back through the rack kernel that owns the backlog."""
    card = rack_workspace(ProjectLayout.at(pd)).kernel_for(hyp_id).get(hyp_id)
    assert card is not None, f"{hyp_id} is not on disk"
    return card


def _view(pd: Path, hyp_id: str) -> HypView:
    """The active-HYP view the reflect/judge consumers render. Absent from it ⇒
    the card is gone or no longer active — both are failures for these tests."""
    views = {h.id: h for h in active_hypotheses(rack_workspace(ProjectLayout.at(pd)))}
    assert hyp_id in views, f"{hyp_id} is not an active hypothesis"
    return views[hyp_id]


def _hyp_ids(pd: Path) -> set[str]:
    """Every HYP card in the catalog, state-blind — the old ``list_all`` claim.
    Globs the dir-per-card layout because the rack facade's only listing
    (``active_hypotheses``) filters by state."""
    return {
        p.parent.name
        for p in (ProjectLayout.at(pd).tracker.base / "backlog" / "hypotheses").glob(
            "HYP-*/task.yaml"
        )
    }


def _flat_files(pd: Path) -> list[Path]:
    return list((ProjectLayout.at(pd).tracker.base / "backlog" / "hypotheses").glob("HYP-*.yaml"))


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


def test_default_mode_writes_without_prompt(project_dir, monkeypatch):
    """Default (no --preview, no --bg) writes the intake immediately."""
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
    res = CliRunner().invoke(reflect, ["issue", "agent skips param queries"])
    assert res.exit_code == 0, res.output
    assert "created HYP-001" in res.output
    # No interactive prompt in default mode
    assert "Write this intake?" not in res.output
    saved = _card(project_dir, "HYP-001")
    assert saved.state == "active"
    assert "source_task" not in saved.links
    assert saved.extras["origin"] == "supervisor-observation"
    assert saved.title.startswith("agent ignores")
    assert saved.extras["exit_criteria"]["confirm"] == ["4 sessions clean"]


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
            "--session",
            "20260512-120000-1",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "merged into HYP-001" in res.output
    saved = _view(project_dir, "HYP-001")
    assert len(saved.validation_log) == 1
    entry = saved.validation_log[0]
    assert entry["verdict"] == "inconclusive"
    assert entry["evidence"].startswith("same pattern")
    assert entry["session_id"] == "20260512-120000-1"
    assert _hyp_ids(project_dir) == {"HYP-001"}  # merged, not a new card


def test_pipeline_failure_with_active_hyps_fails_loud(project_dir, monkeypatch):
    """When dedup is needed but the LLM failed — refuse to write."""
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline_raises(monkeypatch, RuntimeError("simulated network"))
    res = CliRunner().invoke(reflect, ["issue", "some observation"])
    assert res.exit_code != 0
    assert "active hypotheses exist" in res.output
    # No NEW hypothesis was created.
    assert _hyp_ids(project_dir) == {"HYP-001"}


def test_pipeline_failure_no_active_hyps_graceful_degrade(project_dir, monkeypatch):
    """No active HYPs + LLM failed → minimal HYP created."""
    _mock_pipeline_raises(monkeypatch, RuntimeError("api down"))
    res = CliRunner().invoke(reflect, ["issue", "an observation worth keeping"])
    assert res.exit_code == 0, res.output
    saved = _card(project_dir, "HYP-001")
    assert saved.extras["hypothesis"] == "an observation worth keeping"
    assert len(saved.title) <= 60
    assert saved.extras.get("baseline") is None
    assert saved.extras.get("expected_outcome", []) == []


def test_empty_marker_block_triggers_fail_loud(project_dir, monkeypatch):
    """Marker missing → pipeline produced empty intake_result; with active HYP must fail-loud."""
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline(monkeypatch, result_text="", exit_code=0)
    res = CliRunner().invoke(reflect, ["issue", "x"])
    assert res.exit_code != 0
    assert "did not emit" in res.output or "BEGIN_INTAKE_RESULT" in res.output


def test_preview_mode_shows_draft_and_can_abort(project_dir, monkeypatch):
    _mock_pipeline(
        monkeypatch,
        result_text=("action: create\ndraft:\n  title: t\n  hypothesis: h\n"),
    )
    res = CliRunner().invoke(reflect, ["issue", "x", "--preview"], input="n\n")
    assert res.exit_code == 0
    assert "Intake draft:" in res.output
    assert "aborted" in res.output
    assert _hyp_ids(project_dir) == set()


def test_preview_mode_writes_on_yes(project_dir, monkeypatch):
    _mock_pipeline(
        monkeypatch,
        result_text=("action: create\ndraft:\n  title: t\n  hypothesis: h\n"),
    )
    res = CliRunner().invoke(reflect, ["issue", "x", "--preview"], input="y\n")
    assert res.exit_code == 0
    assert "Intake draft:" in res.output
    assert "created HYP-001" in res.output


def test_merge_unknown_target_fails_loud(project_dir, monkeypatch):
    _write_active_hyp(project_dir, "HYP-001")
    _mock_pipeline(
        monkeypatch,
        result_text=("action: merge\ntarget_id: HYP-999\nevidence: hallucinated\n"),
    )
    res = CliRunner().invoke(reflect, ["issue", "x"])
    assert res.exit_code != 0
    assert "HYP-999" in res.output


def test_task_id_overrides_source_task(project_dir, monkeypatch):
    anchor = (
        rack_workspace(ProjectLayout.at(project_dir))
        .kernel_for("HATS-1")
        .create(actor="test", caller_cwd=project_dir, title="anchor")
        .task.id
    )
    _mock_pipeline(
        monkeypatch,
        result_text=("action: create\ndraft:\n  title: t\n  hypothesis: h\n"),
    )
    res = CliRunner().invoke(reflect, ["issue", "obs", "--task", anchor])
    assert res.exit_code == 0, res.output
    card = _card(project_dir, "HYP-001")
    assert card.links["source_task"] == [anchor]
    assert "origin" not in card.extras or not card.extras["origin"]


def test_a_task_id_that_does_not_exist_demotes_to_origin(project_dir, monkeypatch):
    """HATS-1596: this used to write `source_task: [HATS-304]` on a card whose
    target was never created — an edge the kernel refuses on every other write
    path. The observation is still worth keeping, so the id rides `origin`."""
    _mock_pipeline(
        monkeypatch,
        result_text=("action: create\ndraft:\n  title: t\n  hypothesis: h\n"),
    )
    res = CliRunner().invoke(reflect, ["issue", "obs", "--task", "HATS-304"])
    assert res.exit_code == 0, res.output
    card = _card(project_dir, "HYP-001")
    assert "source_task" not in card.links
    assert card.extras["origin"] == "HATS-304"


def test_background_spawns_detached_subprocess_and_returns(
    project_dir,
    monkeypatch,
):
    """--bg invokes subprocess.Popen and returns without running the pipeline."""
    import ai_hats.cli.reflect as mod

    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    import subprocess

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    # If pipeline ran, the test would fail with "no pipeline mock" — assert it
    # was NOT called by leaving _run_intake_pipeline unmocked.
    def boom(*a, **kw):
        raise AssertionError("pipeline must not run in --bg parent")

    monkeypatch.setattr(mod, "_run_intake_pipeline", boom)

    res = CliRunner().invoke(reflect, ["issue", "обс", "--bg", "--task", "HATS-304"])
    assert res.exit_code == 0, res.output
    assert "spawned (pid=4242, bg)" in res.output

    # Subprocess was launched detached with start_new_session and our args
    assert captured["kwargs"]["start_new_session"] is True
    cmd = captured["cmd"]
    # Re-invokes via python -c entry-point trampoline (avoids needing ai-hats
    # binary on PATH inside the test)
    assert cmd[1] == "-c"
    assert "reflect" in cmd and "issue" in cmd and "обс" in cmd
    assert "--task" in cmd and "HATS-304" in cmd
    # --bg must NOT be in the child cmd (would recurse)
    assert "--bg" not in cmd and "--background" not in cmd

    # Log directory was created
    assert (ProjectLayout.at(project_dir).sessions.runs / "reflect-issue").exists()


def test_bg_and_preview_are_mutually_exclusive(project_dir):
    res = CliRunner().invoke(reflect, ["issue", "obs", "--bg", "--preview"])
    assert res.exit_code != 0
    assert "mutually exclusive" in res.output


def test_build_intake_prompt_includes_recent_evidence(project_dir):
    """The prompt fed to Haiku must expose validation_log evidences so dedup
    can see a HYP's effective scope, not just its one-line statement."""
    import json

    from ai_hats.cli.reflect import _build_intake_prompt

    h = HypView(
        id="HYP-001",
        title="t",
        status="active",
        hypothesis="agents miss user feedback on plans",
        success_criterion=None,
        observation_window=None,
        verification_protocol=None,
        validation_log=(
            {
                "date": "2026-05-02",
                "verdict": "inconclusive",
                "evidence": "agent forgot to remove comments after addressing",
            },
            {
                "date": "2026-05-03",
                "verdict": "inconclusive",
                "evidence": "agent skipped user feedback in plan.md iteration",
            },
        ),
    )
    text = _build_intake_prompt("new observation", [h])
    # Pull the JSON section out and validate structure
    _, _, json_block = text.partition("ACTIVE_HYPOTHESES:\n")
    payload = json.loads(json_block.strip())
    assert payload[0]["id"] == "HYP-001"
    assert payload[0]["recent_evidence"] == [
        "agent forgot to remove comments after addressing",
        "agent skipped user feedback in plan.md iteration",
    ]


def test_build_intake_prompt_omits_evidence_when_empty(project_dir):
    """No validation_log entries → no `recent_evidence` key in payload."""
    import json

    from ai_hats.cli.reflect import _build_intake_prompt

    h = HypView(
        id="HYP-001",
        title="t",
        status="active",
        hypothesis="h",
        success_criterion=None,
        observation_window=None,
        verification_protocol=None,
        validation_log=(),
    )
    text = _build_intake_prompt("obs", [h])
    _, _, json_block = text.partition("ACTIVE_HYPOTHESES:\n")
    payload = json.loads(json_block.strip())
    assert "recent_evidence" not in payload[0]
