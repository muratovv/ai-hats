"""HATS-1216 — HITL and Automate sessions persist the launch record to
``<session_dir>/role_materialization.json``.

Moved out of ``tests/e2e/`` by HATS-1493: it drives the runners in-process.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from ai_hats.assembler import Assembler
from ai_hats.cli import main
from ai_hats.models import ProjectConfig
from ai_hats_observe.artifacts import ROLE_MATERIALIZATION_JSON
from ai_hats.paths import PROJECT_CONFIG


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_DIR = REPO_ROOT / "packages" / "ai-hats-library" / "src" / "ai_hats_library"


@pytest.fixture
def project_with_maintainer_default(tmp_path: Path, monkeypatch) -> Path:
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
    # HATS-1493: check_update_async is step 1 of the real human pipeline and
    # would fire a detached network probe from a test that spawns nothing else.
    monkeypatch.setenv("AI_HATS_NO_UPDATE_CHECK", "1")
    return project


def _install_pty_capture(monkeypatch, sink: dict[str, Any]) -> None:
    from ai_hats import runtime as rt

    def _capture(_self, cmd, env, tracer, pty_tap_factory=None, on_spawn=None):  # noqa: ARG001
        sink["cmd"] = list(cmd)
        return 0

    monkeypatch.setattr(rt.WrapRunner, "_pty_spawn", _capture)
    monkeypatch.setattr(
        rt.WrapRunner,
        "_resync_managed_hooks",
        lambda self, session=None, result=None: [],
        raising=False,
    )
    monkeypatch.setenv("AI_HATS_QUIET", "1")


def _find_latest_session_dir(project: Path) -> Path:
    runs = project / ".agent" / "ai-hats" / "sessions" / "runs"
    assert runs.exists(), f"expected session runs root {runs} to exist"
    sdirs = [d for d in runs.iterdir() if d.is_dir() and d.name.startswith("session_")]
    assert len(sdirs) == 1, f"expected exactly 1 session dir under {runs}, got {sdirs}"
    return sdirs[0]


def test_hitl_session_persists_role_materialization_json(
    project_with_maintainer_default: Path, monkeypatch
):
    """HITL launch writes <session_dir>/role_materialization.json (HATS-1216)."""
    sink: dict[str, Any] = {}
    _install_pty_capture(monkeypatch, sink)

    runner = CliRunner()
    res = runner.invoke(main, [])
    assert res.exit_code == 0, f"ai-hats CLI exited with code {res.exit_code}:\n{res.output}"

    sdir = _find_latest_session_dir(project_with_maintainer_default)
    rec_file = sdir / ROLE_MATERIALIZATION_JSON
    assert rec_file.is_file(), f"role_materialization.json missing from {sdir}"

    data = json.loads(rec_file.read_text())
    assert data["role"] == "maintainer"
    assert data["provider"] == "claude"
    assert data["run_mode"] == "hitl"
    assert isinstance(data["launch"], list)
    assert isinstance(data["env_keys"], list)
    assert isinstance(data["materialized"], list)

    for entry in data["materialized"]:
        assert "digest" in entry


def test_automate_subagent_persists_role_materialization_json(
    project_with_maintainer_default: Path, monkeypatch
):
    """Automate (SubAgentRunner) launch writes <session_dir>/role_materialization.json (HATS-1216)."""
    from ai_hats.composition_seam import build_composition_payload
    from ai_hats.runtime import SubAgentRunner
    from ai_hats_observe import SessionManager

    # HATS-1493: no raising=False. This patched a name that never existed, so
    # the stub was inert and the SDK spawned the real `claude` CLI — invisibly,
    # because SubAgentRunner swallows every engine exception.
    monkeypatch.setattr(
        "ai_hats.surfaces.claude.sdk_runner.run_claude_sdk_blocking",
        lambda *args, **kwargs: None,
    )

    payload = build_composition_payload(project_with_maintainer_default, role_override="maintainer")
    session_mgr = SessionManager(
        project_with_maintainer_default,
        runs_dir=ProjectLayout.at(project_with_maintainer_default).sessions.runs,
    )
    runner = SubAgentRunner(
        ProjectLayout.at(project_with_maintainer_default), payload, session_mgr=session_mgr
    )
    session = runner.run(task="test task", isolation_mode="none")

    rec_file = session.role_materialization_path
    assert rec_file.is_file(), f"role_materialization.json missing from {session.session_dir}"

    data = json.loads(rec_file.read_text())
    assert data["role"] == "maintainer"
    assert data["provider"] == "claude"
    assert data["run_mode"] == "automate"
    assert isinstance(data["launch"], list)
    assert isinstance(data["env_keys"], list)
    assert isinstance(data["materialized"], list)

    for entry in data["materialized"]:
        assert "digest" in entry
