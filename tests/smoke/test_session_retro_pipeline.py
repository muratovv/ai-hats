"""Smoke test for the session-retro pipeline dispatch (HATS-418).

Locks the in-process dispatch added in ``_finalize_session`` after the
HATS-294 install-step regression silently broke the
``session_end_auto-retro.sh`` shell hook (see ``HATS-418`` plan).

Coverage:

* Happy path — ``action == "run"`` invokes
  ``_spawn_session_reviewer_background`` with the session id.
* Recursion guard — ``HATS_SKIP_RETRO=1`` in the env suppresses spawn so
  the reviewer's own sub-Claude session does not re-fire dispatch.
* Skip / hint actions do not spawn.
* Spawner itself uses ``start_new_session=True`` so the child survives
  parent-shell ``SIGHUP`` (lifecycle assumption locked at the unit
  boundary; the full terminal-detach proof is the manual e2e step in the
  task plan).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from ai_hats.observe import Session
from ai_hats.paths import runs_dir
from ai_hats.retro import auto_retro
from ai_hats.runtime import _finalize_session


# ---------------------------------------------------------------------------
# Minimal stubs (kept self-contained — tests/smoke/ has no shared conftest).
# ---------------------------------------------------------------------------


class _StubHooksRunner:
    def __init__(self) -> None:
        self.calls: list = []

    def run(self, event, env=None):
        self.calls.append(event)
        return []


class _StubTracer:
    def flush_response(self) -> None:
        return None


def _make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="test", session_dir=session_dir)


def _write_run_policy_yaml(tmp_path: Path) -> None:
    """Force the policy decision to ``run`` so the dispatch branch fires.

    `_finalize_session` re-reads yaml via ``make_decision``; without a
    metrics.json file the policy short-circuits to ``skip`` (HATS-418
    plan rejects the foreground/`background=False` branch as dead, so we
    only need the ``always`` policy to land on ``run``).
    """
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


def _finalize_kwargs(tmp_path: Path) -> dict:
    session = _make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")
    return {
        "session": session,
        "exit_code": 0,
        "active_role": "primary",
        "provider_name": "claude",
        "claude_session_id": "abc-123",
        "project_dir": tmp_path,
        "env": {},
        "hooks_runner": _StubHooksRunner(),
        "tracer": _StubTracer(),
    }


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
    # Ensure no stale recursion guard from outer env leaks in.
    monkeypatch.delenv("HATS_SKIP_RETRO", raising=False)

    _finalize_session(**_finalize_kwargs(tmp_path))

    assert spawned == [(tmp_path, "test")], (
        f"expected one spawn call with (project_dir, session_id), got {spawned}"
    )
    # Discard the session-end summary box — keeps test output clean.
    capsys.readouterr()


@pytest.mark.smoke
def test_recursion_guard_suppresses_dispatch(tmp_path, monkeypatch, capsys):
    """`HATS_SKIP_RETRO=1` in env must skip the in-process spawn.

    The reviewer's sub-Claude session inherits this env var; without the
    guard the runtime would spawn a new reviewer ad-infinitum (HATS-252).
    """
    _write_run_policy_yaml(tmp_path)
    spawned: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        auto_retro, "_spawn_session_reviewer_background",
        lambda pd, sid: spawned.append((pd, sid)),
    )
    monkeypatch.setenv("HATS_SKIP_RETRO", "1")

    _finalize_session(**_finalize_kwargs(tmp_path))

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

    _finalize_session(**_finalize_kwargs(tmp_path))

    assert spawned == []
    capsys.readouterr()


@pytest.mark.smoke
def test_spawner_uses_start_new_session(tmp_path, monkeypatch):
    """`_spawn_session_reviewer_background` must set ``start_new_session=True``.

    This is the kernel-level reason the spawned reviewer survives the
    parent-shell ``SIGHUP`` when the user closes their terminal —
    ``setsid()`` detaches the child from the controlling TTY. Without
    this kwarg the entire pipeline regresses to "auto-retro dies when
    user closes shell". The full terminal-detach behaviour is verified
    by hand (see HATS-418 plan, Verification > Manual e2e), but the
    kwarg presence is locked here so a refactor cannot silently drop it.
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
