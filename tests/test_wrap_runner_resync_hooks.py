"""HATS-707 → HATS-833: the in-session managed-hook drift net.

Re-homed from the dead ``session_start: [ai-hats self sync-hooks]`` lifecycle
channel to a direct ``Assembler.hooks.sync_hooks()`` call at session start in
``WrapRunner._resync_managed_hooks``, then generalized (HATS-833) from git-only
to ALL managed-hook surfaces (runtime + wt + git) with an observable heal note.

These tests drive ``WrapRunner._resync_managed_hooks()`` directly (the seam),
not a full PTY session.
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.wrap_runner import WrapRunner
from ai_hats.paths import PROJECT_CONFIG
from ai_hats_core.layout import ProjectLayout

pytestmark = pytest.mark.integration


def _runner(project: Path) -> WrapRunner:
    """WrapRunner wired with the project's real HooksManager (HATS-865: the
    payload carries it; these seam tests exercise the result-less resync
    edge, so the composition itself is a placeholder)."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats_observe import SessionManager, SidecarTracer
    from ai_hats.paths import runs_dir
    from ai_hats_core import CompositionResult

    payload = CompositionPayload(
        result=CompositionResult(
            name="t",
            priorities=[],
            rules=[],
            skills=[],
            injections=[],
        ),
        provider=None,
        effective_role="t",
        hooks=Assembler(project).hooks,
    )
    return WrapRunner(
        ProjectLayout.at(project),
        payload,
        session_mgr=SessionManager(project, runs_dir=runs_dir(project)),
        tracer_factory=SidecarTracer,
    )


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(path), check=True)


def test_resync_managed_hooks_returns_empty_list(tmp_path):
    """HATS-1480 D5: _resync_managed_hooks is retired and returns [] (no-op)."""
    project = tmp_path / "plain"
    project.mkdir()
    _git_init(project)
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)
    assert _runner(project)._resync_managed_hooks() == []


def test_payload_startup_warnings_surface_as_warn_notices(tmp_path):
    """HATS-970: hooks warnings carried on the payload (first-run compose seam)
    surface as WARN notices for the pre-launch read-hold."""
    from dataclasses import replace

    project = tmp_path / "plain"
    project.mkdir()
    _git_init(project)
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)

    runner = _runner(project)
    runner.payload = replace(
        runner.payload,
        startup_warnings=("core.hooksPath is already set to 'x' — not overwriting",),
    )

    notices = runner._payload_startup_notices()

    assert [n.level for n in notices] == ["warn"]
    assert "core.hooksPath is already set" in notices[0].text


def test_composition_diagnostics_surface_as_notices_of_their_own_level(tmp_path):
    """HATS-1753: the composition states the level, and the banner honours it —
    contrast the neighbouring producers, which hardcode "warn" at the boundary."""
    from dataclasses import replace

    from ai_hats.diagnostics import Diagnostic, Level

    project = tmp_path / "plain"
    project.mkdir()
    _git_init(project)
    ProjectConfig(provider="agy").save(project / PROJECT_CONFIG)

    runner = _runner(project)
    runner.payload = replace(
        runner.payload,
        startup_warnings=("a hooks warning",),
        diagnostics=(
            Diagnostic(Level.WARN, "those rows will never fire", where=Path("/lib/r.yaml")),
            Diagnostic(Level.NOTE, "nothing to worry about"),
        ),
    )

    notices = runner._payload_startup_notices()

    assert [n.level for n in notices] == ["warn", "warn", "note"], "hooks first, then ours"
    assert "/lib/r.yaml: those rows will never fire" == notices[1].text
