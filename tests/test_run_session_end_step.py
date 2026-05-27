"""Tests for the ``RunSessionEnd`` pipeline step (HATS-535, HATS-530).

Post-HATS-530 this step owns SESSION_END hooks + retro reminder banner
only — auto-retro decision/spawn moved to
``MaybeSpawnSessionReviewer`` (see
``tests/test_maybe_spawn_session_reviewer_step.py``). The HATS-086
SIGINT-safety invariants previously enforced inside the
``_finalize_session`` megafunction are still pinned here on the
hooks path.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.models import LifecycleEvent
from ai_hats.observe import Session
from ai_hats.pipeline.steps.run_session_end import RunSessionEnd


def make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    step = RunSessionEnd()
    io = step.io
    assert io.name == "run_session_end"
    assert io.requires == frozenset({
        "session_id", "session_dir", "project_dir",
        "exit_code", "audit_path", "hooks_env",
    })
    # HATS-530: retro_decision is produced upstream by
    # MaybeSpawnSessionReviewer; absent it the banner is silently skipped.
    assert io.optional == frozenset({"retro_decision"})
    assert io.produces == frozenset()


def test_failure_policy_continue():
    assert RunSessionEnd.failure_policy == "continue"


# ---------------------------------------------------------------------------
# Retro decision write-log is now MaybeSpawnSessionReviewer's responsibility
# (HATS-530) — see tests/test_maybe_spawn_session_reviewer_step.py.
# RunSessionEnd here covers hooks + banner only.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# HATS-086 SIGINT-safety: each sub-phase swallowed; step never raises
# ---------------------------------------------------------------------------


def test_does_not_raise_when_hooks_runner_fails(tmp_path, monkeypatch):
    """``hooks_runner.run`` raising MUST NOT propagate."""
    from ai_hats import runtime as runtime_module

    class _ExplodingHooksRunner:
        def __init__(self, hooks_dir, project_dir):
            pass

        def run(self, event, env=None):
            raise RuntimeError("hook boom")

    monkeypatch.setattr(runtime_module, "HooksRunner", _ExplodingHooksRunner)

    session = make_session(tmp_path)
    step = RunSessionEnd()
    # Must not raise.
    step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        project_dir=tmp_path,
        exit_code=0,
        audit_path=session.audit_path,
        hooks_env={},
    )


def test_does_not_raise_when_hooks_runner_keyboard_interrupt(tmp_path, monkeypatch):
    """A second Ctrl+C during hook dispatch MUST NOT propagate."""
    from ai_hats import runtime as runtime_module

    class _InterruptingHooksRunner:
        def __init__(self, hooks_dir, project_dir):
            pass

        def run(self, event, env=None):
            raise KeyboardInterrupt()

    monkeypatch.setattr(runtime_module, "HooksRunner", _InterruptingHooksRunner)

    session = make_session(tmp_path)
    step = RunSessionEnd()
    step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        project_dir=tmp_path,
        exit_code=0,
        audit_path=session.audit_path,
        hooks_env={},
    )  # must not raise


def test_session_end_hooks_dispatched(tmp_path, monkeypatch):
    """Happy path: hooks_runner.run is called with SESSION_END + hooks_env."""
    from ai_hats import runtime as runtime_module

    calls: list = []

    class _RecordingHooksRunner:
        def __init__(self, hooks_dir, project_dir):
            calls.append(("init", hooks_dir, project_dir))

        def run(self, event, env=None):
            calls.append(("run", event, env))
            return []

    monkeypatch.setattr(runtime_module, "HooksRunner", _RecordingHooksRunner)

    session = make_session(tmp_path)
    env = {"AI_HATS_SESSION_ID": "test", "X_CUSTOM": "1"}
    step = RunSessionEnd()
    step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        project_dir=tmp_path,
        exit_code=0,
        audit_path=session.audit_path,
        hooks_env=env,
    )

    run_calls = [c for c in calls if c[0] == "run"]
    assert len(run_calls) == 1
    assert run_calls[0][1] == LifecycleEvent.SESSION_END
    assert run_calls[0][2] == env
