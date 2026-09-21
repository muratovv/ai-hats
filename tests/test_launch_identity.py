"""Session identity is decided at LAUNCH, from the argv the provider built (HATS-1397).

Before this, ``wrap_runner`` minted a uuid4 per session and persisted it for every
surface. Only non-resume claude puts it on its command line — agy ``del``s it,
cline's base ignores it, claude omits it on ``--resume`` — so ``metrics.json``
(and ``audit.md``, and ``session show``) named a session that exists on no
surface. These tests run the REAL providers, not mocks: the assertion is about
what each surface actually does with the id.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_hats.session_artifacts import RunMode, SessionPolicy, consumed_session_id
from ai_hats.session_plan import launch
from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.agy.provider import AgySurface
from ai_hats.surfaces.cline.provider import ClineSurface
from ai_hats.surfaces.plan import Launch, MaterializationPlan
from ai_hats_core.layout import ProjectLayout
from ai_hats_observe.session import Session
from tests._plan_helpers import composition_with, flags

SID = "11111111-2222-3333-4444-555555555555"


def _cmd(provider, *, resume: bool = False, tmp_path: Path) -> list[str]:
    """The argv the launch pair yields for a plan with no session args of its own."""
    composition = composition_with("r")
    root = tmp_path / "sessions" / "s"
    plan = MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface=provider.name,
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=(),
        env={},
        launch=Launch(args=(), sdk_options=None),
    )
    launched = launch(
        plan,
        flags(
            root,
            provider_session_id=SID,
            extra_args=("--resume",) if resume else (),
            claim=False,
        ),
        layout=ProjectLayout.at(tmp_path / "proj"),
    )
    return list(launched.args)


def test_claude_consumes_the_session_id(tmp_path: Path) -> None:
    cmd = _cmd(ClaudeSurface(), tmp_path=tmp_path)

    assert SID in cmd
    assert consumed_session_id(cmd, SID) == SID


def test_claude_resume_does_not_consume_it(tmp_path: Path) -> None:
    """``--resume`` reattaches claude's own prior session, so our id never reaches it."""
    cmd = _cmd(ClaudeSurface(), resume=True, tmp_path=tmp_path)

    assert SID not in cmd
    assert consumed_session_id(cmd, SID) == ""


@pytest.mark.parametrize("provider", [AgySurface(), ClineSurface()], ids=["agy", "cline"])
def test_surfaces_that_drop_the_session_id_claim_no_identity(provider, tmp_path: Path) -> None:
    """The F12 shape: a uuid4 was minted and persisted for surfaces that never saw it."""
    cmd = _cmd(provider, tmp_path=tmp_path)

    assert SID not in cmd
    assert consumed_session_id(cmd, SID) == ""


def _session(tmp_path: Path) -> Session:
    d = tmp_path / "session_20260731-000000-1"
    d.mkdir()
    s = Session(session_id=d.name, session_dir=d)
    s.init_audit(role="assistant", provider="claude")
    return s


def test_identity_is_persisted_at_launch_not_at_teardown(tmp_path: Path) -> None:
    """A session killed before teardown must still name its transcript."""
    s = _session(tmp_path)

    s.record_provider_session_id(SID)

    metrics = json.loads(s.metrics_path.read_text())
    assert metrics["claude_session_id"] == SID
    # The launch stub's own fields survive — this is an update, not a rewrite.
    assert metrics["role"] == "assistant"
    assert metrics["finalized"] is False


def test_an_unclaimed_identity_is_not_persisted(tmp_path: Path) -> None:
    s = _session(tmp_path)

    s.record_provider_session_id("")

    assert "claude_session_id" not in json.loads(s.metrics_path.read_text())
