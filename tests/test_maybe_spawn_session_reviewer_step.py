"""Tests for the ``MaybeSpawnSessionReviewer`` pipeline step (HATS-530).

Covers the auto-retro decision + spawn block extracted from
``RunSessionEnd`` so HITL and SubAgent pipelines can share it.
Mirrors the HATS-086 SIGINT-safety pattern previously enforced
inside ``RunSessionEnd``.
"""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import os
from pathlib import Path

import yaml

from ai_hats_observe import Session
from ai_hats.pipeline.steps.maybe_spawn_session_reviewer import (
    MaybeSpawnSessionReviewer,
)
from ai_hats.constants import ENV_SKIP_RETRO
from ai_hats_observe.artifacts import METRICS_JSON, RETRO_LOG
from ai_hats.paths import PROJECT_CONFIG


def _pin_project(monkeypatch, root):
    """The sync branch deserializes the session's project — pin the factory to the fixture."""
    from ai_hats.config.project import ProjectConfig
    from ai_hats.project import Project
    from ai_hats_core.layout import ProjectLayout

    layout = ProjectLayout.at(root)
    monkeypatch.setattr(
        "ai_hats.cli._entry.resolve_project",
        lambda *a, **k: Project(
            layout=layout, config=ProjectConfig(), venv=layout.default_venv, library_paths=()
        ),
    )


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
    (tmp_path / PROJECT_CONFIG).write_text(
        yaml.dump(
            {
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
            }
        )
    )
    metrics_dir = ProjectLayout.at(tmp_path).sessions.runs / "session_test"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return metrics_dir / METRICS_JSON


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    step = MaybeSpawnSessionReviewer()
    io = step.io
    assert io.name == "maybe_spawn_session_reviewer"
    assert io.requires == frozenset({"session_id", "layout"})
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
        layout=ProjectLayout.at(tmp_path),
    )

    log = ProjectLayout.at(tmp_path).sessions.runs / "session_test" / RETRO_LOG
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
    """Threshold met, ``background=True`` (default) → detached Popen path
    invoked; the synchronous in-process path (HATS-1402) is NOT used."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=True)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    sync_calls: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.cli.reflect_session_main.run_session_review",
        lambda sid, max_retries, layout, **kw: sync_calls.append((sid, max_retries, layout.root)),
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
    )

    assert spawned == [(ProjectLayout.at(tmp_path), "test")]
    assert sync_calls == [], "background=True must use the detached Popen path, not sync"
    assert delta["retro_decision"]["action"] == "run"
    assert delta["retro_decision"]["background"] is True


# ---------------------------------------------------------------------------
# Sync in-process run — background: false (HATS-1402)
# ---------------------------------------------------------------------------


def test_background_false_runs_sync_in_process(tmp_path, monkeypatch):
    """``background: false`` → ``run_session_review`` is called in-process
    (max_retries=1, matching the Popen path's default), and the detached
    ``_spawn_session_reviewer_background`` path is NOT used."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=False)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    spawned: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    sync_calls: list[tuple] = []
    monkeypatch.setattr(
        "ai_hats.cli.reflect_session_main.run_session_review",
        lambda sid, max_retries, layout, **kw: sync_calls.append((sid, max_retries, layout.root)),
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    delta = step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
    )

    assert sync_calls == [("test", 1, tmp_path)]
    assert spawned == [], "background=False must not use the detached Popen path"
    assert delta["retro_decision"]["action"] == "run"
    assert delta["retro_decision"]["background"] is False


def test_background_false_sets_and_clears_recursion_guard(tmp_path, monkeypatch):
    """The recursion guard is set to ``"1"`` only for the duration of the
    synchronous call, then cleared — a caller running many sessions
    in-process (e.g. a test loop) must not be left with a stuck guard."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=False)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    seen_during_call: list[str | None] = []

    def _fake_run_session_review(sid, max_retries, layout, **kw):
        seen_during_call.append(os.environ.get(ENV_SKIP_RETRO))

    monkeypatch.setattr(
        "ai_hats.cli.reflect_session_main.run_session_review",
        _fake_run_session_review,
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
    )

    assert seen_during_call == ["1"], "guard must be set to '1' for the duration of the sync call"
    assert os.environ.get(ENV_SKIP_RETRO) is None, "guard must be cleared after run() returns"


def test_background_false_sync_failure_does_not_raise(tmp_path, monkeypatch):
    """A ``run_session_review`` exception on the sync path must not
    propagate (mirrors the existing spawn-failure invariant) and the
    recursion guard must still be cleared afterward."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=False)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    def _boom(sid, max_retries, layout, **kw):
        raise RuntimeError("sync boom")

    monkeypatch.setattr(
        "ai_hats.cli.reflect_session_main.run_session_review",
        _boom,
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    # Must not raise.
    delta = step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
    )

    assert delta["retro_decision"]["action"] == "run"
    assert os.environ.get(ENV_SKIP_RETRO) is None, "guard must be cleared even after a failure"


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
        layout=ProjectLayout.at(tmp_path),
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
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    # Must not raise.
    delta = step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
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
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    # Must not raise.
    step.run(
        session_id=session.session_id,
        layout=ProjectLayout.at(tmp_path),
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
        layout=ProjectLayout.at(tmp_path),
    )
    # When decision fails before any output, the delta is empty —
    # downstream optional consumer ``run_session_end`` handles this
    # by silently skipping the retro banner.
    assert delta == {}


# ---------------------------------------------------------------------------
# Evidence survives an interrupted decision (HATS-1426)
# ---------------------------------------------------------------------------


def test_breadcrumb_lands_before_the_decision(tmp_path, monkeypatch):
    """The incident left NO retro.log at all: the runtime line was written only
    after ``make_decision``, so an abort inside it erased the whole trace."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    def _interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("ai_hats.retro.auto_retro.make_decision", _interrupted)

    step = MaybeSpawnSessionReviewer()
    delta = step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))

    log = ProjectLayout.at(tmp_path).sessions.runs / "session_test" / RETRO_LOG
    assert log.exists(), "an interrupted decision must still leave a trace"
    assert "runtime\tstart" in log.read_text()
    assert delta == {}


# ---------------------------------------------------------------------------
# Outcome journal (HATS-1487): the decision line records intent, not outcome
# ---------------------------------------------------------------------------


def _retro_log(tmp_path: Path) -> str:
    return (ProjectLayout.at(tmp_path).sessions.runs / "session_test" / RETRO_LOG).read_text()


def test_outcome_suppressed_by_guard_is_journalled(tmp_path, monkeypatch):
    """`decision run` + guard set: the journal must say the spawn did NOT happen."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: None,
    )
    monkeypatch.setenv(ENV_SKIP_RETRO, "1")

    step = MaybeSpawnSessionReviewer()
    step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))

    content = _retro_log(tmp_path)
    assert "runtime\toutcome\tsuppressed-by-guard" in content
    assert "HATS_SKIP_RETRO='1'" in content
    assert content.index("decision") < content.index("outcome"), "outcome follows the decision"


def test_outcome_spawn_bg_is_journalled(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=True)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    monkeypatch.setattr(
        "ai_hats.retro.auto_retro._spawn_session_reviewer_background",
        lambda pd, sid: None,
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))

    assert "runtime\toutcome\tspawn-bg" in _retro_log(tmp_path)


def test_outcome_sync_done_carries_the_return_code(tmp_path, monkeypatch):
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=False)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    monkeypatch.setattr(
        "ai_hats.cli.reflect_session_main.run_session_review",
        lambda sid, max_retries, layout, **kw: 0,
    )
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))

    content = _retro_log(tmp_path)
    assert "runtime\toutcome\tsync-start" in content
    assert "runtime\toutcome\tsync-done (rc=0)" in content


def test_outcome_sync_failed_is_journalled(tmp_path, monkeypatch):
    """The failure already only whispered into a logger — journal it too."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path, min_turns=1, min_tool_calls=1, background=False)
    metrics.write_text(json.dumps({"turns": 5, "tool_calls": 10}))

    def _boom(sid, max_retries, layout, **kw):
        raise RuntimeError("sync boom")

    monkeypatch.setattr("ai_hats.cli.reflect_session_main.run_session_review", _boom)
    _pin_project(monkeypatch, tmp_path)
    monkeypatch.delenv(ENV_SKIP_RETRO, raising=False)

    step = MaybeSpawnSessionReviewer()
    step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))  # must not raise

    content = _retro_log(tmp_path)
    assert "runtime\toutcome\tsync-failed" in content
    assert "sync boom" in content
    assert os.environ.get(ENV_SKIP_RETRO) is None, "guard must still be cleared"


def test_no_outcome_line_for_a_skip_decision(tmp_path):
    """R3: a skip decision IS its own outcome — no second line."""
    session = _make_session(tmp_path)
    metrics = _seed_project(tmp_path)
    metrics.write_text(json.dumps({"turns": 0, "tool_calls": 0}))

    step = MaybeSpawnSessionReviewer()
    step.run(session_id=session.session_id, layout=ProjectLayout.at(tmp_path))

    assert "outcome" not in _retro_log(tmp_path)
