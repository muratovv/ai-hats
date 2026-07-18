"""Release-on-finish: a finished sub-agent drops its ownership hold (HATS-1045).

Sequential sub-agents run in ONE ``SubAgentRunner`` process and share
``ENV_ROOT_PID`` (= this runner's ``os.getpid()``). Without a release at the
session boundary, a finished session's hold reads live to ``record_is_live`` and
refuses a sibling's reclaim of the same task. ``_run_attempt``'s per-session
``finally`` calls ``_release_ownership_on_finish`` to drop only this session's
own ``(session_id, root_pid)`` holds, fail-open.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ai_hats.runtime import SubAgentRunner
from ai_hats.tracker_wiring import tracker_paths
from ai_hats_tracker import ownership


@dataclass
class _FakeSession:
    session_id: str
    logs: list[str] = field(default_factory=list)

    def log_sys(self, msg: str) -> None:
        self.logs.append(msg)


def _runner(project_dir: Path) -> SubAgentRunner:
    runner = SubAgentRunner.__new__(SubAgentRunner)
    runner.project_dir = project_dir
    return runner


def _registry(project_dir: Path) -> Path:
    reg = tracker_paths(project_dir).tasks_dir.parent / "ownership.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    return reg


def test_release_on_finish_drops_own_hold(tmp_path: Path) -> None:
    reg = _registry(tmp_path)
    ownership.take(reg, "T-1", "sess-a", os.getpid())
    _runner(tmp_path)._release_ownership_on_finish(_FakeSession("sess-a"))
    assert ownership.owner_of(reg, "T-1") is None


def test_release_on_finish_lets_sibling_reclaim(tmp_path: Path) -> None:
    """The retry-loop repro: attempt 1 (sess-a) claims T and finishes without a
    terminal transition; attempt 2 (sess-b, SAME runner pid) must now claim T
    instead of hitting OwnershipRefused."""
    reg = _registry(tmp_path)
    pid = os.getpid()
    ownership.take(reg, "T-1", "sess-a", pid)
    _runner(tmp_path)._release_ownership_on_finish(_FakeSession("sess-a"))
    ownership.take(reg, "T-1", "sess-b", pid)  # no OwnershipRefused
    assert ownership.owner_of(reg, "T-1")["session_id"] == "sess-b"


def test_release_on_finish_is_fail_open(tmp_path: Path, monkeypatch) -> None:
    """An ownership error during teardown is swallowed and logged — it must
    never propagate to mask the sub-agent result or skip cache cleanup."""

    def _boom(*_a, **_k):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(ownership, "release_session_pid", _boom)
    session = _FakeSession("sess-a")
    _runner(tmp_path)._release_ownership_on_finish(session)  # does not raise
    assert any("release-on-finish" in line for line in session.logs)
