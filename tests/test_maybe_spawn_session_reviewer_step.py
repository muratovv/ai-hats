"""Tests for the ``MaybeSpawnSessionReviewer`` pipeline step (HATS-530).

Covers the auto-retro decision + spawn block extracted from
``RunSessionEnd`` so HITL and SubAgent pipelines can share it.
Mirrors the HATS-086 SIGINT-safety pattern previously enforced
inside ``RunSessionEnd``.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from ai_hats_observe import Session
from ai_hats.paths import runs_dir
from ai_hats.pipeline.steps.maybe_spawn_session_reviewer import (
    MaybeSpawnSessionReviewer,
)
from ai_hats.constants import ENV_SKIP_RETRO
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, RETRO_LOG


def _make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def _seed_project(
    tmp_path: Path,
    *,
    min_turns: int = 1,
    min_tool_calls: int = 1,
    policy: str = "smart",
    background: bool = True,
) -> Path:
    """Write ai-hats.yaml + ensure session dir; return path to metrics.json."""
    (tmp_path / PROJECT_CONFIG).write_text(yaml.dump({
        "schema_version": 2,
        "provider": "claude",
        "active_role": "primary",
        "feedback": {
            "session_retro": {
                "policy": policy,
                "smart_threshold": {
                    "min_turns": min_turns,
                    "min_tool_calls": min_tool_calls,
                },
                "mode": "programmatic",
                "background": background,
            },
        },
    }))
    metrics_dir = runs_dir(tmp_path) / "session_test"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return metrics_dir / METRICS_JSON


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    step = MaybeSpawnSessionReviewer()
    io = step.io
    assert io.name == "maybe_spawn_session_reviewer"
    assert io.requires == frozenset({"session_id", "project_dir"})
    assert io.produces == frozenset({"retro_decision"})


def test_failure_policy_continue():
    assert MaybeSpawnSessionReviewer.failure_policy == "continue"


# ---------------------------------------------------------------------------
# Decision write-log (HATS-158 invariant — log lands before spawn)
# ---------------------------------------------------------------------------


def test_writes_runtime_decision_line_for_skip(tmp_path):
    """Short session (turns=0) → decision is 'skip', log written."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 0, "tool_calls": 0}))

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )

    log = runs_dir(tmp_path) / "session_test" / RETRO_LOG
    assert log.exists()
    content = log.read_text()
    assert "runtime\tdecision" in content
    assert "skip" in content
    # Decision is still emitted into the funnel even for skip.
    assert "retro_decision" in delta
    assert delta["retro_decision"]["action"] == "skip"


# ---------------------------------------------------------------------------
# Spawn — fires when threshold met (positive case)
# ---------------------------------------------------------------------------


def test_spawns_reviewer_when_threshold_met(tmp_path, monkeypatch):
    """Threshold met → ``_spawn_session_reviewer_background`` invoked."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )

    assert spawned == [(tmp_path, "test")]
    assert delta["retro_decision"]["action"] == "run"


def test_recursion_guard_blocks_spawn(tmp_path, monkeypatch):
    """``HATS_SKIP_RETRO=1`` → spawn NOT called even when threshold met.

    Decision still computed + logged + returned — only the spawn side
    effect is gated by the recursion guard. This preserves the
    HATS-252 invariant that a session-reviewer sub-process does not
    re-trigger its own reviewer.
    """
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.setenv(ENV_SKIP_RETRO, "1")

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )

    assert spawned == [], "recursion guard must block spawn"
    # But the decision IS still produced — guard is spawn-only.
    assert delta["retro_decision"]["action"] == "run"


# ---------------------------------------------------------------------------
# HATS-086 SIGINT-safety: each sub-phase swallowed; step never raises
# ---------------------------------------------------------------------------


def test_does_not_raise_when_spawn_fails(tmp_path, monkeypatch):
    """Spawn raising MUST NOT propagate."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    def _boom(pd, sid):
        raise RuntimeError("spawn boom")

    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        _boom,
    )
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    # Must not raise.
    delta = step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )
    # Decision still emitted despite spawn crash.
    assert delta["retro_decision"]["action"] == "run"


def test_does_not_raise_when_spawn_keyboard_interrupt(tmp_path, monkeypatch):
    """A second Ctrl+C during spawn MUST NOT propagate."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    def _interrupt(pd, sid):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        _interrupt,
    )
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    # Must not raise.
    step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )


def test_does_not_raise_when_make_decision_fails(tmp_path, monkeypatch):
    """make_decision raising MUST NOT propagate; step returns empty delta.

    ``make_decision`` is documented as "never raises" (it captures into
    ``action="skip"``), so to test this invariant we patch it. The
    point of the test is the step's defensive try/except — the
    upstream contract change shouldn't break the pipeline.
    """
    session = _make_session(tmp_path)
    _seed_project(tmp_path)

    def _boom(pd, sid):
        raise RuntimeError("decision boom")

    monkeypatch.setattr(
        "ai_hats.retro.auto_retro.make_decision",
        _boom,
    )

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )
    # When decision fails before any output, the delta is empty —
    # downstream optional consumer ``run_session_end`` handles this
    # by silently skipping the retro banner.
    assert delta == {}
