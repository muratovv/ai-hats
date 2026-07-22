"""Tests for the ``ComputeUsage`` pipeline step (HATS-664).

Sibling of ``test_make_audit_step.py``. Verifies the live-session driver:
JSONL resolution (configured + discovery fallback), usage.json persistence,
the StepIO contract, fail-soft behaviour, and routing through the injected
surface parser (HATS-953). The pure parser is covered separately in the
package's ``test_usage.py``.
"""

from __future__ import annotations

import calendar
import json
import os
from pathlib import Path
from types import SimpleNamespace

from ai_hats.surfaces.claude.provider import ClaudeProvider
from ai_hats_observe import Session
from ai_hats.pipeline.steps.compute_usage import ComputeUsage
from ai_hats_observe.artifacts import METRICS_JSON, USAGE_JSON

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"

# HATS-1087: discovery moved to the provider; the step no longer has a
# Claude-specific fallback. Tests inject the real resolver, mirroring the
# production path (composition_seam threads provider.resolve_transcript).
_claude_resolver = ClaudeProvider().resolve_transcript


def make_session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "session_test"
    session_dir.mkdir()
    return Session(session_id="20260605-100000-1", session_dir=session_dir)


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
    io = ComputeUsage().io
    assert io.name == "compute_usage"
    assert io.requires == frozenset({
        "session_id", "session_dir", "claude_session_id", "project_dir",
    })
    assert io.optional == frozenset({
        "role", "static_cost_analyzer", "audit_writer_factory",
        "transcript_resolver",
    })
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
        transcript_resolver=_claude_resolver,
    )

    usage_path = session.session_dir / USAGE_JSON
    assert delta == {"usage_path": usage_path}
    assert usage_path.exists()
    report = json.loads(usage_path.read_text())
    assert report["schema_version"] == "usage/v1"
    assert report["aggregates"]["skill_loads"] == {"backlog-manager": 1}
    assert report["aggregates"]["tool_success_rate"] == 0.75


def test_falls_back_to_discovered_jsonl_when_configured_path_missing(
    tmp_path, monkeypatch,
):
    """Resume-mode regression (HATS-272 / HATS-734): the configured
    ``claude_session_id`` is a uuid4 that never reached Claude, so its path is
    missing; ``_discover_claude_jsonl`` must pick the most-recent JSONL under
    the project_key dir using the ai-hats ``session_id`` (NOT the uuid) for the
    mtime-window start.

    Before HATS-734 the step passed ``claude_session_id`` to discovery, which
    fed a uuid to ``strptime("%Y%m%d-%H%M%S")`` → ValueError → None → the
    fallback was permanently dead and ``usage.json`` was silently skipped in
    exactly the resume scenario the fallback exists for. Sibling ``make_audit``
    passes ``session_id`` correctly; this asserts ``compute_usage`` converged.

    Fail-under-revert: pass the uuid back to ``_discover_claude_jsonl`` →
    discovery returns None → no usage.json → both asserts below fail.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)  # session_id = 20260605-100000-1
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)

    # JSONL lives under Claude's OWN uuid (foreign to claude_session_id), with
    # mtime AFTER the session start so the discovery window accepts it.
    real_jsonl = claude_dir / "real-claude-uuid.jsonl"
    real_jsonl.write_text((TRANSCRIPTS / "normal.jsonl").read_text())
    _set_mtime(real_jsonl, calendar.timegm((2026, 6, 5, 11, 0, 0, 0, 0, 0)))

    delta = ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id="dead-uuid-never-passed-to-claude",
        project_dir=project_dir,
        transcript_resolver=_claude_resolver,
    )

    usage_path = session.session_dir / USAGE_JSON
    assert delta == {"usage_path": usage_path}, (
        "HATS-734: discovery must run off session_id, not the claude uuid"
    )
    assert usage_path.exists()
    report = json.loads(usage_path.read_text())
    assert report["schema_version"] == "usage/v1"


def test_session_meta_filled_from_metrics_json(tmp_path, monkeypatch):
    """role/provider/exit_code are copied from the session's metrics.json
    (written upstream) so usage.json self-describes which composition ran."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    (session.session_dir / METRICS_JSON).write_text(
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
        transcript_resolver=_claude_resolver,
    )

    report = json.loads((session.session_dir / USAGE_JSON).read_text())
    assert report["role"] == "maintainer"
    assert report["provider"] == "claude"
    assert report["exit_code"] == 0


def test_funnel_role_overrides_metrics_json(tmp_path, monkeypatch):
    """A live ``role`` from the pipeline funnel wins over the persisted value."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    (session.session_dir / METRICS_JSON).write_text(
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
        transcript_resolver=_claude_resolver,
        role="live-role",
    )

    report = json.loads((session.session_dir / USAGE_JSON).read_text())
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
        transcript_resolver=_claude_resolver,
    )

    report = json.loads((session.session_dir / USAGE_JSON).read_text())
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
        transcript_resolver=_claude_resolver,
    )

    assert delta == {}
    assert not (session.session_dir / USAGE_JSON).exists()


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

    # ClaudeParser.parse_usage calls usage.parse_session_usage via the module,
    # so patching the source symbol reaches it.
    monkeypatch.setattr("ai_hats_observe.usage.parse_session_usage", _boom)

    delta = ComputeUsage().run(  # must not raise
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
        transcript_resolver=_claude_resolver,
    )
    assert delta == {}


def test_routes_through_injected_parser(tmp_path, monkeypatch):
    """usage/v1 is produced by the injected surface parser (HATS-953).

    RED-under-revert: re-inlining parse_session_usage into the step (bypassing
    audit_writer_factory().parser) makes the sentinel below unreachable.
    """
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    session = make_session(tmp_path)
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    claude_dir = _claude_dir_for(tmp_path / "home", project_dir)
    csid = "routed-uuid"
    (claude_dir / f"{csid}.jsonl").write_text(
        (TRANSCRIPTS / "normal.jsonl").read_text()
    )

    class _FakeParser:
        def parse_usage(self, jsonl_path, trace_path):
            return {"schema_version": "usage/v1", "source": "injected-parser", "flags": []}

    ComputeUsage().run(
        session_id=session.session_id,
        session_dir=session.session_dir,
        claude_session_id=csid,
        project_dir=project_dir,
        transcript_resolver=_claude_resolver,
        audit_writer_factory=lambda: SimpleNamespace(parser=_FakeParser()),
    )

    report = json.loads((session.session_dir / USAGE_JSON).read_text())
    assert report["source"] == "injected-parser"
