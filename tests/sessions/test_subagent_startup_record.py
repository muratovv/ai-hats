"""An Automate session has no banner, so what recovery did at ``create_session``
reaches ``diagnostics.json["startup"]`` — the same record the HITL path keeps."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats_core.diagnostics import Diagnostic, Level
from ai_hats_core.layout import ProjectLayout

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(project / PROJECT_CONFIG)
    asm = Assembler(project, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(project)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return project


class _Reporting:
    def run(self):
        return (Diagnostic(Level.NOTE, "runs retention: dropped 2 files / 140 bytes"),)


def test_automate_session_persists_recovery_diagnostics(project: Path, monkeypatch):
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.runtime import SubAgentRunner
    from ai_hats_observe import SessionManager

    monkeypatch.setattr(
        "ai_hats.surfaces.claude.sdk_runner.run_claude_sdk_blocking",
        lambda *args, **kwargs: None,
    )
    payload = build_composition_payload(project, role_override="maintainer")
    session_mgr = SessionManager(
        project, runs_dir=ProjectLayout.at(project).sessions.runs, recovery=_Reporting()
    )
    runner = SubAgentRunner(ProjectLayout.at(project), payload, session_mgr=session_mgr)

    session = runner.run(task="test task", isolation_mode="none")

    record = json.loads((session.session_dir / "diagnostics.json").read_text())
    assert record["startup"] == {
        "hold_seconds": 0.0,
        "notices": [{"level": "note", "text": "runs retention: dropped 2 files / 140 bytes"}],
    }
