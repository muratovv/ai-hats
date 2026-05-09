"""Routing tests with mocked WrapRunner / SubAgentRunner.

Verifies that the four entry points all funnel through `execute`'s dispatch
helpers:

- bare `ai-hats`            → WrapRunner.run, role_override=None, extra_args=[]
- `ai-hats reflect all`     → WrapRunner.run, role_override="judge",
                              extra_args[0] contains preamble + handoff
- `ai-hats agent <role>`    → SubAgentRunner.run via _do_execute (regression)
- `ai-hats reflect session` → SubAgentRunner.run via SessionReviewRunner
                              (regression — specialized path, not under execute)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from ai_hats.cli import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    pd.mkdir()
    (pd / ".gitlog").mkdir()
    (pd / ".agent" / "hypotheses").mkdir(parents=True)
    (pd / ".agent" / "backlog" / "proposals").mkdir(parents=True)
    (pd / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: test-agent\n"
    )
    monkeypatch.chdir(pd)
    return pd


# ---------- bare `ai-hats` ----------


def test_bare_ai_hats_routes_to_wraprunner(
    project_dir: Path, monkeypatch
) -> None:
    """HATS-267: bare ai-hats now goes through human.yaml pipeline, but the
    underlying runner dispatch must still land on WrapRunner with the same
    arguments."""
    captured: dict = {}

    class _StubSession:
        session_id = "20260101-000000-1"
        session_dir = project_dir / ".gitlog" / "session_x"
        trace_path = session_dir / "trace.log"

    class _WrapRunner:
        def __init__(self, _pd): pass

        def run(self, provider, **kwargs):
            captured["provider"] = provider
            captured.update(kwargs)
            (project_dir / ".gitlog").mkdir(parents=True, exist_ok=True)
            _StubSession.session_dir.mkdir(parents=True, exist_ok=True)
            _StubSession.trace_path.write_text("")
            return 0, _StubSession

    class _SubAgentRunner:  # must NOT be called
        def __init__(self, _pd):
            raise AssertionError("SubAgentRunner must not be invoked from bare ai-hats")

        def run(self, **_kw):
            raise AssertionError("SubAgentRunner.run must not be invoked")

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "WrapRunner", _WrapRunner)
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _SubAgentRunner)
    # bootstrap_or_die touches stuff; stub it
    import ai_hats._bootstrap as boot
    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)
    # spawn_session_review uses subprocess.Popen — stub it
    import subprocess
    from unittest.mock import MagicMock
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: MagicMock(pid=99))

    res = CliRunner().invoke(main, [])
    assert res.exit_code == 0, res.output
    assert captured["role_override"] is None
    assert captured["provider"] == "claude"
    assert captured["extra_args"] == []


# ---------- `ai-hats reflect all` ----------


def _make_hyp(pd: Path, hyp_id: str):
    body = {
        "id": hyp_id, "title": f"hyp-{hyp_id}",
        "status": "active", "created": "2026-01-01",
        "source_task": "HATS-001", "hypothesis": "h",
        "validation_log": [],
        "success_criterion": "x",
        "observation_window": "5 sessions",
    }
    (pd / ".agent" / "hypotheses" / f"{hyp_id}.yaml").write_text(
        yaml.safe_dump(body)
    )


def _make_prop(pd: Path, pid: str):
    body = {
        "id": pid,
        "created": datetime(2026, 5, 4, tzinfo=timezone.utc).isoformat(),
        "title": f"title-{pid}", "category": "rule", "target": "x",
        "description": "d", "rationale": "r",
        "votes": [], "status": "open",
    }
    (pd / ".agent" / "backlog" / "proposals" / f"{pid}.yaml").write_text(
        yaml.safe_dump(body)
    )


def test_reflect_all_routes_to_wraprunner_with_judge(
    project_dir: Path, monkeypatch
) -> None:
    _make_hyp(project_dir, "HYP-001")
    _make_prop(project_dir, "PROP-001")
    captured: dict = {}

    class _WrapRunner:
        def __init__(self, _pd): pass

        def run(self, provider, **kwargs):
            captured["role_override"] = kwargs.get("role_override")
            captured["extra_args"] = kwargs.get("extra_args")
            return 0, _StubSession(
                project_dir / ".gitlog" / "session_judge",
                {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "WrapRunner", _WrapRunner)
    # spawn_session_review fires after launch_provider — stub Popen.
    import subprocess
    from unittest.mock import MagicMock
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: MagicMock(pid=1))

    res = CliRunner().invoke(main, ["reflect", "all"])
    assert res.exit_code == 0, res.output
    assert captured["role_override"] == "judge"
    # First positional arg = combined prompt (preamble + handoff)
    first_arg = captured["extra_args"][0]
    assert "Reflect-all triage session" in first_arg  # preamble from initial_injections
    assert "HYP-001" in first_arg                       # handoff content
    assert "PROP-001" in first_arg                      # handoff content
    # Handoff file also written to disk (existing behavior)
    handoff_files = list(
        (project_dir / ".agent" / "retrospectives" / "reflect-all").glob(
            "*-handoff.md"
        )
    )
    assert len(handoff_files) == 1


# ---------- `ai-hats agent <role>` ----------


class _StubSession:
    def __init__(self, session_dir: Path, metrics: dict) -> None:
        self.session_id = session_dir.name.removeprefix("session_")
        self.session_dir = session_dir
        session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = session_dir / "trace.log"
        self.trace_path.write_text("(stub)")
        self.metrics_path = session_dir / "metrics.json"
        self.metrics_path.write_text(json.dumps(metrics))


def test_agent_routes_to_subagent_runner(
    project_dir: Path, monkeypatch
) -> None:
    captured: dict = {}

    class _SubAgentRunner:
        def __init__(self, _pd): pass

        def run(self, **kwargs):
            captured.update(kwargs)
            return _StubSession(
                project_dir / ".gitlog" / "session_x", {"exit_code": 0},
            )

    import ai_hats.runtime as runtime_mod
    monkeypatch.setattr(runtime_mod, "SubAgentRunner", _SubAgentRunner)

    res = CliRunner().invoke(
        main, ["agent", "session-reviewer", "--task", "do something"],
    )
    assert res.exit_code == 0, res.output
    assert captured["role_name"] == "session-reviewer"
    assert captured["task"] == "do something"


# ---------- `ai-hats reflect session` (regression) ----------


def test_reflect_session_uses_session_review_runner(
    project_dir: Path, monkeypatch
) -> None:
    """Regression: `reflect session` keeps its specialized path; not under execute.

    We assert SessionReviewRunner is the entry point (not _do_execute), and
    that it's called with the expected session_id.
    """
    captured: dict = {}

    class _StubSessionReviewRunner:
        def __init__(self, _pd): pass

        def run(self, session_id, max_retries=1):
            captured["session_id"] = session_id
            captured["max_retries"] = max_retries
            return project_dir / ".agent" / "retrospectives" / "fake.md"

    # reflect session now goes through PipelineHarness → run_session_review
    # step which lazy-imports from the retro module — patch at the source.
    import ai_hats.retro.session_review_runner as srr
    monkeypatch.setattr(srr, "SessionReviewRunner", _StubSessionReviewRunner)

    sid = "20260507-120000-1"
    res = CliRunner().invoke(main, ["reflect", "session", "--session", sid])
    assert res.exit_code == 0, res.output
    assert captured["session_id"] == sid
