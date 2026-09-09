"""Rendered STATE.md, pinned on the PRODUCTION rack path (HATS-1264).

Salvaged from the retired tracker-era ``tests/test_state.py``
(``state_md_path`` assertions + ``sync``). The subject is now the live
integrator wiring — ``build_rack_kernel``'s ``DerivedViewsExtension`` and the
CLI provider's post-create refresh — resolving its own tracker layout, not an
injected path and not a hand-rolled renderer. ``ai-hats-rack``'s own
``tests/test_views.py`` pins the extension in isolation; what dies here is a
wired kernel that drops the subscriber, or points it at the wrong STATE.md.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import subprocess
from pathlib import Path

import pytest

from ai_hats.rack_cli_provider import CliKernelProvider
from ai_hats.rack_wiring import build_rack_kernel
from ai_hats.tracker_wiring import tracker_paths
from ai_hats_rack.resolver import RackRoot

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 — fixed argv, test helper
        ["git", *args],  # noqa: S607 — git from PATH, as everywhere
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A real repo with a bare ``.agent/`` and no ambient session/dir pins."""
    for var in ("AI_HATS_DIR", "AI_HATS_PROJECT_DIR", "AI_HATS_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)
    p = tmp_path / "project"
    p.mkdir()
    _git(p, "init", "-b", "master")
    _git(p, "config", "user.email", "t@t.t")
    _git(p, "config", "user.name", "t")
    (p / "README.md").write_text("# t")
    _git(p, "add", ".")
    _git(p, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    tracker_paths(ProjectLayout.at(p)).tasks_dir.mkdir(parents=True)
    return p


def _kernel(project: Path):
    """No ``tasks_dir`` / ``state_md_path`` override — the kernel resolves the
    tracker layout itself, exactly as the ``rack`` binary gets it."""
    return build_rack_kernel(
        ProjectLayout.at(project), backlog_owner=ProjectLayout.at(project), prefix="T"
    )


def _root(project: Path) -> RackRoot:
    return RackRoot(
        project_dir=project,
        tasks_dir=tracker_paths(ProjectLayout.at(project)).tasks_dir,
        backlog_owner=project,
        prefix="T",
    )


def test_created_tasks_render_with_priority_and_state(project):
    """Every card is listed under its state heading, priority included."""
    kernel = _kernel(project)
    kernel.create(
        actor="test", caller_cwd=project, task_id="T-1", title="First task", priority="high"
    )
    result = kernel.create(actor="test", caller_cwd=project, task_id="T-2", title="Second task")
    state_md = tracker_paths(ProjectLayout.at(project)).state_md_path
    assert not state_md.exists(), "create takes no FSM edge — the view has not run yet"

    CliKernelProvider().after_create(_root(project), result)

    body = state_md.read_text(encoding="utf-8")
    assert "## BRAINSTORM" in body
    assert "- T-1 [high] First task" in body
    assert "- T-2 [medium] Second task" in body


def test_cancelled_task_renders_under_its_own_heading(project):
    """A terminal edge through the wired kernel re-files the card itself."""
    kernel = _kernel(project)
    kernel.create(
        actor="test",
        caller_cwd=project,
        task_id="T-1",
        title="Show me in STATE.md",
        priority="high",
    )

    kernel.transition("T-1", "cancelled", actor="test", caller_cwd=project, resolution="dup")

    body = tracker_paths(ProjectLayout.at(project)).state_md_path.read_text(encoding="utf-8")
    assert "## CANCELLED" in body
    assert "- T-1 [high] Show me in STATE.md" in body
    assert "## BRAINSTORM" not in body


def test_state_md_is_rebuilt_whole_from_the_cards_on_disk(project):
    """``sync``'s heir: a lost index comes back complete on the next edge, and
    a moved card is re-filed rather than appended twice."""
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="Task 1")
    result = kernel.create(actor="test", caller_cwd=project, task_id="T-2", title="Task 2")
    state_md = tracker_paths(ProjectLayout.at(project)).state_md_path
    CliKernelProvider().after_create(_root(project), result)
    state_md.unlink()

    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)

    body = state_md.read_text(encoding="utf-8")
    assert "## PLAN" in body
    assert "- T-1 [medium] Task 1" in body
    assert "## BRAINSTORM" in body
    assert "- T-2 [medium] Task 2" in body
    assert body.count("T-1") == 1, "the index is regenerated, not appended to"
