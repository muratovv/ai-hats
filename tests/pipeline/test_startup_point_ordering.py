"""HATS-1581 R5: WHEN the startup gate fires within a real HITL launch.

Two halves, both of which a call site can get wrong silently:

1. The launch record is written BEFORE the gate runs. A doctor that reads the
   declaration artifact before it exists reports green over an empty file.
2. The gate is handed the environment the session will actually get. Judging a
   different one is the "green at startup, red at the first transition" split.

Asserted by driving the real launch, not by reading the source: an ordering
proved by inspection stops being proved the moment someone moves a line.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats_observe.artifacts import ROLE_MATERIALIZATION_JSON

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(
        provider="claude",
        library_paths=[str(LIBRARY_DIR)],
        ai_hats_dir=".agent/ai-hats",
        active_role="maintainer",
        default_role="maintainer",
    ).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[LIBRARY_DIR])
    asm.init()
    asm.set_role("maintainer", provider_name="claude")
    monkeypatch.chdir(proj)
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("AI_HATS_QUIET", "1")
    return proj


def _launch(monkeypatch, seen: dict[str, Any]) -> None:
    """Drive a real HITL launch, recording what the gate saw when it was called."""
    from ai_hats import runtime as rt
    from ai_hats import wrap_runner as wr

    def _spy(project_dir, *, session_dir, identity=None, extra_env=None, compose=None):  # noqa: ARG001
        seen["record_exists"] = (Path(session_dir) / ROLE_MATERIALIZATION_JSON).is_file()
        seen["extra_env"] = dict(extra_env or {})
        return []

    monkeypatch.setattr(wr, "run_startup_checks", _spy)
    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", lambda *a, **k: 0)
    monkeypatch.setattr(
        rt.WrapRunner,
        "_resync_managed_hooks",
        lambda self, session=None, result=None: [],
        raising=False,
    )


def test_the_gate_runs_after_the_launch_record_is_written(project, monkeypatch):
    """R5 first half — the declaration the doctor reads must already be on disk."""
    seen: dict[str, Any] = {}
    _launch(monkeypatch, seen)

    res = CliRunner().invoke(main, [])

    assert res.exit_code == 0, res.output
    assert seen.get("record_exists") is True, (
        "the startup gate ran before the launch record existed — a doctor reading "
        "the declaration would report green over nothing"
    )


def test_the_gate_is_handed_the_sessions_own_environment(project, monkeypatch):
    """R5 second half — the gate must judge the env the session will get."""
    seen: dict[str, Any] = {}
    _launch(monkeypatch, seen)

    res = CliRunner().invoke(main, [])

    assert res.exit_code == 0, res.output
    assert seen.get("extra_env"), "the gate was handed no session environment at all"
