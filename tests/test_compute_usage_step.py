"""Tests for the ``ComputeUsage`` pipeline step (HATS-664).

Sibling of ``test_make_audit_step.py``. Verifies the live-session driver:
JSONL resolution (configured + discovery fallback), usage.json persistence,
the StepIO contract, and fail-soft behaviour. The pure parser is covered
separately in ``test_usage.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai_hats.observe import Session
from ai_hats.pipeline.steps.compute_usage import ComputeUsage

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"


def make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="20260605-100000-1", session_dir=session_dir)


def _claude_dir_for(home: Path, project_dir: Path) -> Path:
    project_key = str(project_dir).replace("/", "-")
    d = home / ".claude" / "projects" / project_key
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# StepIO contract
# ---------------------------------------------------------------------------


def test_io_contract():
    io = ComputeUsage().io
    assert io.name == "compute_usage"
    assert io.requires == frozenset({
        "session_id", "session_dir", "claude_session_id", "project_dir",
    })
    assert io.optional == frozenset({"role"})
    assert io.produces == frozenset({"usage_path"})


def test_failure_policy_continue():
    assert ComputeUsage.failure_policy == "continue"


# ---------------------------------------------------------------------------
# Behaviour: writes usage.json from the resolved JSONL
# ---------------------------------------------------------------------------


def test_writes_usage_json_from_configured_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    csid = "abc-direct-uuid"
    (claude_dir / f"{csid}.jsonl").write_text(
        (TRANSCRIPTS / "normal.jsonl").read_text()
    )

    delta = ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
    )

    usage_path = session.session_dir / "usage.json"
    assert delta == {"usage_path": usage_path}
    assert usage_path.exists()
    report = json.loads(usage_path.read_text())
    assert report["schema_version"] == "usage/v1"
    assert report["aggregates"]["skill_loads"] == {"backlog-manager": 1}
    assert report["aggregates"]["tool_success_rate"] == 0.75


def test_session_meta_filled_from_metrics_json(tmp_path, monkeypatch):
    """role/provider/exit_code are copied from the session's metrics.json
    (written upstream) so usage.json self-describes which composition ran."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    (session.session_dir / "metrics.json").write_text(
        json.dumps({"role": "maintainer", "provider": "claude", "exit_code": 0})
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    csid = "meta-uuid"
    (claude_dir / f"{csid}.jsonl").write_text(
        (TRANSCRIPTS / "normal.jsonl").read_text()
    )

    ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
    )

    report = json.loads((session.session_dir / "usage.json").read_text())
    assert report["role"] == "maintainer"
    assert report["provider"] == "claude"
    assert report["exit_code"] == 0


def test_funnel_role_overrides_metrics_json(tmp_path, monkeypatch):
    """A live ``role`` from the pipeline funnel wins over the persisted value."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    (session.session_dir / "metrics.json").write_text(
        json.dumps({"role": "stale-role", "provider": "claude"})
    )
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    csid = "override-uuid"
    (claude_dir / f"{csid}.jsonl").write_text(
        (TRANSCRIPTS / "normal.jsonl").read_text()
    )

    ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
        role="live-role",
    )

    report = json.loads((session.session_dir / "usage.json").read_text())
    assert report["role"] == "live-role"


def test_session_meta_null_when_no_metrics(tmp_path, monkeypatch):
    """No metrics.json → metadata stays null, no crash."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    csid = "nometa-uuid"
    (claude_dir / f"{csid}.jsonl").write_text(
        (TRANSCRIPTS / "normal.jsonl").read_text()
    )

    ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
    )

    report = json.loads((session.session_dir / "usage.json").read_text())
    assert report["role"] is None
    assert report["provider"] is None


def test_missing_jsonl_returns_empty_delta_no_crash(tmp_path, monkeypatch):
    """No JSONL anywhere → best-effort no-op, empty delta, no usage.json, no raise."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    _claude_dir_for(tmp_path / "home", project_dir)  # dir exists, no files

    delta = ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id="nonexistent-uuid",
        project_dir=project_dir,
    )

    assert delta == {}
    assert not (session.session_dir / "usage.json").exists()


def test_parser_exception_is_swallowed(tmp_path, monkeypatch):
    """A parser blow-up must not propagate (failure_policy=continue)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    csid = "boom-uuid"
    (claude_dir / f"{csid}.jsonl").write_text("{}")

    def _boom(_path):
        raise RuntimeError("parser boom")

    monkeypatch.setattr(
        "ai_hats.pipeline.steps.compute_usage.parse_session_usage",
        _boom,
        raising=False,
    )
    # parse_session_usage is imported inside run(); patch the source symbol.
    monkeypatch.setattr("ai_hats.usage.parse_session_usage", _boom)

    delta = ComputeUsage().run(  # must not raise
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
    )
    assert delta == {}
