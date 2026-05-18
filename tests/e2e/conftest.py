"""Shared e2e helpers for HATS-269 regression suite.

Mocking strategy: at the runner boundary (WrapRunner / SubAgentRunner /
SessionReviewRunner) plus subprocess.Popen. Pipeline core / harness /
steps run for real — these tests are the regression catcher for the
migration, so they must observe end-to-end side-effects.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_hats.paths import hypotheses_dir, proposals_dir, retros_dir, runs_dir


class _StubSession:
    def __init__(
        self,
        project_dir: Path,
        session_id: str = "20260101-000000-1",
        exit_code: int = 0,
    ) -> None:
        self.session_id = session_id
        self.session_dir = runs_dir(project_dir) / f"session_{session_id}"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.session_dir / "trace.log"
        self.trace_path.write_text("(trace)")
        self.metrics_path = self.session_dir / "metrics.json"
        self.metrics_path.write_text(
            json.dumps({"exit_code": exit_code, "session_id": session_id,
                        "role": "test", "duration_s": 0.1})
        )


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    pd = tmp_path / "proj"
    pd.mkdir()
    runs_dir(pd).mkdir(parents=True, exist_ok=True)
    (hypotheses_dir(pd)).mkdir(parents=True)
    (proposals_dir(pd)).mkdir(parents=True)
    (pd / "ai-hats.yaml").write_text(
        "schema_version: 2\nprovider: claude\nactive_role: test-agent\n"
    )
    monkeypatch.chdir(pd)
    return pd


@pytest.fixture
def captured() -> dict:
    return {
        "wrap_calls": [],
        "sub_calls": [],
        "session_review_calls": [],
        "popen_calls": [],
    }


@pytest.fixture
def mock_runners(monkeypatch, project_dir, captured):
    """Mock WrapRunner / SubAgentRunner / SessionReviewRunner / Popen.

    Returns the captured dict so tests can assert what was called with.
    """
    pd = project_dir
    cap = captured

    class _WrapRunner:
        def __init__(self, _pd): pass

        def run(self, provider, **kwargs):
            cap["wrap_calls"].append({"provider": provider, **kwargs})
            return 0, _StubSession(pd, "wrap-1")

    class _SubAgentRunner:
        def __init__(self, _pd): pass

        def run(self, **kwargs):
            cap["sub_calls"].append(kwargs)
            return _StubSession(pd, "sub-1")

    class _SessionReviewRunner:
        def __init__(self, _pd): pass

        def run(self, sid, max_retries=1, harness_policy=None):
            del harness_policy  # accepted for API parity, unused by stubs
            cap["session_review_calls"].append((sid, max_retries))
            out = retros_dir(pd) / "sessions" / f"{sid}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                "---\n"
                "session_id: " + sid + "\n"
                "summary: ok\n"
                "hypothesis_verdicts: []\n"
                "---\n\nbody\n"
            )
            return out

    import ai_hats.retro.session_review_runner as srr
    import ai_hats.runtime as rt
    monkeypatch.setattr(rt, "WrapRunner", _WrapRunner)
    monkeypatch.setattr(rt, "SubAgentRunner", _SubAgentRunner)
    monkeypatch.setattr(srr, "SessionReviewRunner", _SessionReviewRunner)

    def _popen_stub(*args, **kwargs):
        cap["popen_calls"].append({"args": args[0] if args else None,
                                    "kwargs": kwargs})
        return MagicMock(pid=999)

    import subprocess
    monkeypatch.setattr(subprocess, "Popen", _popen_stub)

    # bootstrap_or_die for bare ai-hats
    import ai_hats._bootstrap as boot
    monkeypatch.setattr(boot, "bootstrap_or_die", lambda: None)

    return cap
