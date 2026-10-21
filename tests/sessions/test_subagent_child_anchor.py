"""The engine path anchors the surface child's pid: a subprocess engine's child
outlives a SIGKILLed wrapper (pipes, no tty) and keeps reading the session
cache — without its pid in the anchor, the next run's sweep reaps a LIVE
session's dir and its gates fail open mid-flight."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.surfaces import SurfaceRunResult
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
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return project


class _Reporting:
    def run(self):
        return ()


def test_engine_branch_anchors_the_surface_child(project: Path):
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.runtime import SubAgentRunner
    from ai_hats.session_liveness import ANCHOR_NAME
    from ai_hats.surfaces.claude.provider import ClaudeSurface
    from ai_hats_observe import SessionManager

    observed = {}

    class _ProbingEngine:
        def run(self, *, layout, session_id, on_spawn=None, **kwargs):
            assert on_spawn is not None, "the engine branch must hand in on_spawn"
            on_spawn(os.getpid())
            anchor = layout.cache.session(session_id) / ANCHOR_NAME
            observed["anchor"] = json.loads(anchor.read_text())
            return SurfaceRunResult(
                exit_code=0, stdout="ok", stderr="", timed_out=False, error=None
            )

    class _NoSdk(ClaudeSurface):
        def engine(self):
            return _ProbingEngine()

        def readiness_findings(self, environ):
            return []  # the engine is stubbed: no binary to ask

    payload = replace(
        build_composition_payload(project, role_override="maintainer"), provider=_NoSdk()
    )
    session_mgr = SessionManager(
        project, runs_dir=ProjectLayout.at(project).sessions.runs, recovery=_Reporting()
    )
    runner = SubAgentRunner(ProjectLayout.at(project), payload, session_mgr=session_mgr)

    runner.run(task="test task", isolation_mode="none")

    assert observed["anchor"]["child_pid"] == os.getpid()
