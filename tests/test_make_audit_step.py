"""Tests for the ``MakeAudit`` pipeline step (HATS-535).

Migration target for the AuditWriter+JSONL-discovery tests that lived
in ``test_runtime_session_end.py`` pre-refactor. The step is the sole
post-spawn audit derivation surface — for both HITL (via
``finalize-hitl``) and SubAgent (via ``finalize-subagent``).
"""

from __future__ import annotations

import calendar
import os
from pathlib import Path


from ai_hats import observe as observe_module
from ai_hats.observe import Session
from ai_hats.pipeline.steps.make_audit import MakeAudit


def make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="20260507-154102-1", session_dir=session_dir)


def _claude_dir_for(home: Path, project_dir: Path) -> Path:
    project_key = str(project_dir).replace("/", "-")
    d = home / ".claude" / "projects" / project_key
    d.mkdir(parents=True)
    return d


def _set_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    step = MakeAudit()
    io = step.io
    assert io.name == "make_audit"
    assert io.requires == frozenset({
        "session_id", "session_dir", "claude_session_id",
        "project_dir", "exit_code",
    })
    assert io.produces == frozenset({"audit_path"})


def test_failure_policy_continue():
    """``make_audit`` is best-effort — must not halt the pipeline on error."""
    assert MakeAudit.failure_policy == "continue"


# ---------------------------------------------------------------------------
# Behaviour: invokes AuditWriter with the right JSONL path
# ---------------------------------------------------------------------------


def test_passes_configured_jsonl_path_when_present(tmp_path, monkeypatch):
    """When ``--session-id`` was injected (claude_session_id directly
    matches the JSONL), AuditWriter receives that exact path."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    csid = "abc-direct-uuid"
    real_jsonl = claude_dir / f"{csid}.jsonl"
    real_jsonl.write_text("{}")

    captured: dict = {}

    class _CapturingAuditWriter:
        def build(self, session, jsonl_path=None, keep_raw=False):
            captured["jsonl_path"] = jsonl_path
            captured["session"] = session

    monkeypatch.setattr(observe_module.AuditWriter, "build", _CapturingAuditWriter().build)

    step = MakeAudit()
    delta = step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
        exit_code=0,
    )

    assert captured["jsonl_path"] == real_jsonl
    assert delta == {"audit_path": session.audit_path}


def test_falls_back_to_discovered_jsonl_when_configured_path_missing(
    tmp_path, monkeypatch,
):
    """Resume-mode regression (HATS-272): configured ``claude_session_id``
    points nowhere; ``_discover_claude_jsonl`` picks the most-recent
    JSONL under the project_key dir, and that path is what
    ``AuditWriter`` receives."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    session.init_audit(role="primary", provider="claude")

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    real_jsonl = claude_dir / "real-claude-uuid.jsonl"
    real_jsonl.write_text("{}")
    _set_mtime(real_jsonl, calendar.timegm((2026, 5, 7, 16, 0, 0, 0, 0, 0)))

    captured: dict = {}

    class _CapturingAuditWriter:
        def build(self, session, jsonl_path=None, keep_raw=False):
            captured["jsonl_path"] = jsonl_path

    monkeypatch.setattr(observe_module.AuditWriter, "build", _CapturingAuditWriter().build)

    step = MakeAudit()
    step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id="dead-uuid-never-passed-to-claude",
        project_dir=project_dir,
        exit_code=0,
    )

    assert captured["jsonl_path"] == real_jsonl, (
        "HATS-272 resume-mode JSONL discovery must still work after "
        "HATS-535 extraction into MakeAudit."
    )


def test_swallows_audit_writer_exception(tmp_path, monkeypatch):
    """AuditWriter raising MUST NOT propagate — failure_policy is continue
    but the step itself wraps the body too (HATS-086 invariant inherited
    from pre-refactor ``_finalize_session``)."""
    session = make_session(tmp_path)

    class _ExplodingAuditWriter:
        def build(self, *args, **kwargs):
            raise RuntimeError("audit boom")

    monkeypatch.setattr(observe_module, "AuditWriter", _ExplodingAuditWriter)

    step = MakeAudit()
    # Must not raise.
    delta = step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id="any",
        project_dir=tmp_path,
        exit_code=0,
    )
    # Even on failure, the contract returns audit_path (may not exist on disk).
    assert delta == {"audit_path": session.audit_path}


def test_swallows_audit_writer_keyboard_interrupt(tmp_path, monkeypatch):
    """A second Ctrl+C during AuditWriter.build MUST NOT propagate."""
    session = make_session(tmp_path)

    class _InterruptingAuditWriter:
        def build(self, *args, **kwargs):
            raise KeyboardInterrupt()

    monkeypatch.setattr(observe_module, "AuditWriter", _InterruptingAuditWriter)

    step = MakeAudit()
    step.run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id="any",
        project_dir=tmp_path,
        exit_code=0,
    )  # must not raise
