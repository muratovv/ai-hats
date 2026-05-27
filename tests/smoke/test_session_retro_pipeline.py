"""Smoke test for the session-retro pipeline dispatch (HATS-418, HATS-535, HATS-530).

Locks the in-process session-reviewer dispatch. Originally added in
HATS-418 inside ``runtime._finalize_session`` after HATS-294's
install-step regression silently broke the legacy
``session_end_auto-retro.sh`` shell hook. HATS-535 split the dispatch
out into the ``RunSessionEnd`` step of the ``finalize-hitl`` sub-
pipeline. HATS-530 then **extracted the decision+spawn block again**
into its own ``MaybeSpawnSessionReviewer`` step, so both
``finalize-hitl`` (HITL) and ``finalize-subagent`` (SubAgent) can
share it — closing the asymmetry where SubAgent sessions never auto-
retroed. Contract is still unchanged; only the step boundary moved.

These tests therefore drive ``MaybeSpawnSessionReviewer.run(...)``
directly, NOT ``RunSessionEnd`` — the latter retained only SESSION_END
hooks and the retro banner UI after the HATS-530 split.

Coverage:

* Happy path — ``action == "run"`` invokes
  ``_spawn_session_reviewer_background`` with the session id.
* Recursion guard — ``HATS_SKIP_RETRO=1`` in the env suppresses spawn
  so the reviewer's own sub-Claude session does not re-fire dispatch.
* Skip / hint actions do not spawn.
* Spawner itself uses ``start_new_session=True`` so the child survives
  parent-shell ``SIGHUP``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from ai_hats.observe import Session
from ai_hats.paths import runs_dir
from ai_hats.pipeline.steps.maybe_spawn_session_reviewer import (
    MaybeSpawnSessionReviewer,
)
from ai_hats.retro import auto_retro


def _make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def _write_run_policy_yaml(tmp_path: Path) -> None:
    """Force the policy decision to ``run`` so the dispatch branch fires."""
    (tmp_path / "ai-hats.yaml").write_text(yaml.dump({
        "schema_version": 2,
        "provider": "claude",
        "active_role": "primary",
        "feedback": {
            "session_retro": {
                "policy": "always",
                "mode": "programmatic",
                "background": True,
            },
        },
    }))


def _run_step(tmp_path: Path) -> None:
    """Drive the auto-retro decision + spawn step in isolation.

    Post-HATS-530 surface: spawning lives in
    ``MaybeSpawnSessionReviewer``. This helper exists so the three
    dispatch-shape tests below share an identical setup and only the
    monkeypatched spawner / env differs between them.
    """
    session = _make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")
    step = MaybeSpawnSessionReviewer()
    step.run(
        session_id=session.session_id,
        project_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.smoke
def test_run_action_dispatches_session_reviewer(tmp_path, monkeypatch, capsys):
    """`action == "run"` → `_spawn_session_reviewer_background(...)` invoked."""
    _write_run_policy_yaml(tmp_path)
    spawned: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        auto_retro, "_spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.delenv("HATS_SKIP_RETRO", raising=False)

    _run_step(tmp_path)

    assert spawned == [(tmp_path, "test")], (
        f"expected one spawn call with (project_dir, session_id), got {spawned}"
    )
    capsys.readouterr()


@pytest.mark.smoke
def test_recursion_guard_suppresses_dispatch(tmp_path, monkeypatch, capsys):
    """`HATS_SKIP_RETRO=1` in env must skip the in-process spawn (HATS-252)."""
    _write_run_policy_yaml(tmp_path)
    spawned: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        auto_retro, "_spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.setenv("HATS_SKIP_RETRO", "1")

    _run_step(tmp_path)

    assert spawned == [], (
        "HATS_SKIP_RETRO=1 must short-circuit dispatch to avoid spawn-loop"
    )
    capsys.readouterr()


@pytest.mark.smoke
def test_skip_action_does_not_dispatch(tmp_path, monkeypatch, capsys):
    """Policy=off → action=skip → no spawn (negative control)."""
    (tmp_path / "ai-hats.yaml").write_text(yaml.dump({
        "schema_version": 2,
        "provider": "claude",
        "active_role": "primary",
        "feedback": {
            "session_retro": {
                "policy": "off",
                "mode": "programmatic",
                "background": True,
            },
        },
    }))
    spawned: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        auto_retro, "_spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.delenv("HATS_SKIP_RETRO", raising=False)

    _run_step(tmp_path)

    assert spawned == []
    capsys.readouterr()


@pytest.mark.smoke
def test_spawner_uses_start_new_session(tmp_path, monkeypatch):
    """`_spawn_session_reviewer_background` must set ``start_new_session=True``.

    Kernel-level guarantee that the reviewer survives parent-shell SIGHUP.
    Locked at the unit boundary; full terminal-detach proof is the manual
    e2e step (HATS-418 plan).
    """
    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kw):  # noqa: ANN001 — test stub
        captured["start_new_session"] = kw.get("start_new_session")
        captured["env"] = kw.get("env")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    auto_retro._spawn_session_reviewer_background(tmp_path, "SID")

    assert captured["start_new_session"] is True, (
        "start_new_session=True is the SIGHUP-immunity guarantee — do not "
        "drop it without re-validating the terminal-detach e2e step."
    )
    assert captured["env"]["HATS_SKIP_RETRO"] == "1"
    log = runs_dir(tmp_path) / "session_SID" / "retro.log"
    assert "session-reviewer\tspawn" in log.read_text()
