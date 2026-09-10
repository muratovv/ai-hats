"""HATS-1575: the reflect/judge road carries the check executor too.

``rack_workspace`` is not a read-only facade — ``set_proposal_status`` walks a
PROP along a named FSM edge, and ``kernel_for`` builds whatever backlog an id
routes to. Mounted without a ``check_port`` it ran every bound gate of every
backlog as a silent no-op, the tasks catalog included: this road passes no
``kernel_builder``, so even the tasks instance fell through to the portable kit.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

from pathlib import Path

import pytest

from ai_hats.paths.constants import ENV_AI_HATS_DIR, PROJECT_CONFIG
from ai_hats.rack_workspace import ensure_backlog, rack_workspace
from ai_hats_rack.dispatch import Phase
from ai_hats_rack.selectors import Edge


@pytest.fixture(autouse=True)
def _no_env_optin(monkeypatch):
    monkeypatch.delenv(ENV_AI_HATS_DIR, raising=False)


def _project(tmp_path: Path) -> Path:
    (tmp_path / PROJECT_CONFIG).write_text("schema_version: 4\nprovider: claude\n")
    return tmp_path


def _in_lock_names(kernel, event_key: str) -> set[str]:
    source, target = event_key.split("->")
    return {
        sub.name
        for sub in kernel._dispatcher.subscribers_for_edge(Edge(source, target), Phase.IN_LOCK)
    }


def test_judge_road_mounts_the_check_executor(tmp_path):
    assert rack_workspace(ProjectLayout.at(_project(tmp_path))).check_port is not None


def test_tasks_kernel_on_this_road_is_gated(tmp_path):
    """No kernel_builder here, so the tasks instance takes the portable path —
    the road on which it had no check subscriber at all."""
    ws = rack_workspace(ProjectLayout.at(_project(tmp_path)))
    assert "checks" in _in_lock_names(ws.kernel_for("HATS-1"), "review->done")


def test_sibling_kernel_on_this_road_is_gated(tmp_path):
    """The PROP backlog `set_proposal_status` transitions."""
    project = _project(tmp_path)
    ensure_backlog(ProjectLayout.at(project), "proposals")
    ws = rack_workspace(ProjectLayout.at(project))
    kernel = ws.kernel_for("PROP-1")
    edge = next(iter(kernel.topology.edges["open"]))
    assert "checks" in _in_lock_names(kernel, f"open->{edge}")
