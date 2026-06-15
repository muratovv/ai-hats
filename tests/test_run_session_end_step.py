"""Tests for the ``RunSessionEnd`` pipeline step (HATS-535, HATS-530, HATS-707).

This step owns the retro reminder banner only. Auto-retro decision/spawn
moved to ``MaybeSpawnSessionReviewer`` (HATS-530, see
``tests/test_maybe_spawn_session_reviewer_step.py``); SESSION_END lifecycle
hook dispatch was removed in HATS-707 when the dead ``hooks:`` composition
channel was deleted (it had zero runtime consumers). The HATS-086
SIGINT-safety invariant — the step never propagates — is pinned here on the
banner path.
"""

from __future__ import annotations

from ai_hats.pipeline.steps import run_session_end as rse_module
from ai_hats.pipeline.steps.run_session_end import RunSessionEnd


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    step = RunSessionEnd()
    io = step.io
    assert io.name == "run_session_end"
    # HATS-707: SESSION_END hooks dispatch removed → no mandatory inputs.
    assert io.requires == frozenset()
    # HATS-530: retro_decision is produced upstream by
    # MaybeSpawnSessionReviewer; absent it the banner is silently skipped.
    assert io.optional == frozenset({"retro_decision"})
    assert io.produces == frozenset()


def test_failure_policy_continue():
    assert RunSessionEnd.failure_policy == "continue"


# ---------------------------------------------------------------------------
# Retro reminder banner
# ---------------------------------------------------------------------------


def test_banner_printed_when_decision_present(capsys):
    RunSessionEnd().run(
        retro_decision={"reminder": {"count": 3, "command": "ai-hats reflect"}}
    )
    out = capsys.readouterr().out
    assert "3 sessions" in out
    assert "ai-hats reflect" in out


def test_no_banner_and_no_raise_when_decision_absent(capsys):
    RunSessionEnd().run()  # retro_decision defaults to None
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# HATS-086 SIGINT-safety: banner failure swallowed; step never raises
# ---------------------------------------------------------------------------


def test_does_not_raise_when_banner_fails(monkeypatch):
    def _boom(_retro):
        raise RuntimeError("banner boom")

    monkeypatch.setattr(rse_module, "_print_retro_banner", _boom)
    # Must not raise.
    RunSessionEnd().run(retro_decision={"reminder": {"count": 1, "command": "x"}})


def test_does_not_raise_when_banner_keyboard_interrupt(monkeypatch):
    def _interrupt(_retro):
        raise KeyboardInterrupt()

    monkeypatch.setattr(rse_module, "_print_retro_banner", _interrupt)
    RunSessionEnd().run(retro_decision={"reminder": {"count": 1, "command": "x"}})  # must not raise
