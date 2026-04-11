"""E2E smoke test: session → retro → bundle → judge feedback loop."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_hats.retro.auto_retro import should_run
from ai_hats.retro.builder import BuilderMode, SessionRetroBuilder
from ai_hats.retro.bundles import BundleManager
from ai_hats.retro.judge import JUDGE_DELIM_END, JUDGE_DELIM_START, JudgeRunner
from ai_hats.retro.judge_retro import JudgeRetroV1
from ai_hats.retro.loader import load

SESSION_ID = "20260410-120000-1"


# --- helpers ---


class _FakeSession:
    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir


class FakeSubAgentRunner:
    def __init__(self, project_dir: Path, transcripts: list[str]) -> None:
        self.project_dir = project_dir
        self._transcripts = list(transcripts)
        self.calls: list[dict] = []
        self._counter = 0

    def run(
        self,
        role_name: str,
        task: str = "",
        ticket_id: str = "",
        model: str = "",
        parent_session: str | None = None,
        isolation_mode: str = "discard",
    ) -> _FakeSession:
        self.calls.append({"role_name": role_name, "task": task})
        self._counter += 1
        sdir = self.project_dir / ".gitlog" / f"session_fake_{self._counter:04d}"
        sdir.mkdir(parents=True, exist_ok=True)
        if self._transcripts:
            (sdir / "transcript.txt").write_text(self._transcripts.pop(0))
        return _FakeSession(sdir)


def _setup_project(project: Path) -> None:
    """Create minimal project structure."""
    (project / ".gitlog").mkdir()
    (project / ".agent" / "retrospectives" / "sessions" / "programmatic").mkdir(parents=True)
    (project / ".agent" / "retrospectives" / "bundles").mkdir(parents=True)
    (project / ".agent" / "retrospectives" / "judge").mkdir(parents=True)


def _write_config(project: Path, *, policy: str = "smart") -> None:
    import yaml
    data = {
        "schema_version": 2,
        "provider": "claude",
        "active_role": "assistant",
        "default_role": "",
        "library_paths": [],
        "feedback": {
            "session_retro": {
                "policy": policy,
                "smart_threshold": {"min_turns": 3, "min_tool_calls": 5},
                "background": True,
                "mode": "programmatic",
            },
            "judge": {"policy": "manual"},
        },
    }
    with open(project / "ai-hats.yaml", "w") as f:
        yaml.dump(data, f)


def _create_session(project: Path, session_id: str, *, turns: int = 6, tool_calls: int = 15) -> Path:
    sdir = project / ".gitlog" / f"session_{session_id}"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "audit.md").write_text(
        f"# Session Audit: {session_id}\n## Turn 1\nuser: do something\nassistant: done\n"
    )
    (sdir / "metrics.json").write_text(json.dumps({
        "role": "assistant",
        "turns": turns,
        "tool_calls": tool_calls,
        "exit_code": 0,
        "tokens": {"input": 1200, "output": 4800},
    }))
    return sdir


def _valid_judge_retro(bundle_id: str, session_id: str) -> str:
    return f"""---
schema: hats-judge-retro/v1
judge_run_id: judge-smoke-001
project: smoke-test
date: '2026-04-10'
bundle_id: {bundle_id}
findings:
  - id: F1
    title: Example finding
    category: process
    severity: low
    root_cause: Example root cause
    evidence:
      - session_id: {session_id}
        source: audit
        location: audit.md:Turn 1
---

# Judge Retrospective

Smoke test body.
"""


# --- test ---


@pytest.mark.smoke
def test_feedback_loop_session_to_judge(tmp_path: Path) -> None:
    """Full loop: session → should_run → retro → bundle → judge."""
    project = tmp_path
    _setup_project(project)
    _write_config(project, policy="smart")

    # 1. Create synthetic session
    _create_session(project, SESSION_ID, turns=6, tool_calls=15)

    # 2. Policy decision: should_run returns 'run'
    action, reason = should_run(
        project / "ai-hats.yaml",
        project / ".gitlog" / f"session_{SESSION_ID}" / "metrics.json",
    )
    assert action == "run", f"Expected 'run', got '{action}': {reason}"

    # 3. Generate programmatic retro
    builder = SessionRetroBuilder(project)
    retro_path = builder.build_and_save(SESSION_ID, mode=BuilderMode.PROGRAMMATIC)
    assert retro_path.exists()

    # 4. Create bundle
    bm = BundleManager(project)
    bundle = bm.create(
        [SESSION_ID],
        now=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
    )
    assert bundle.bundle_id
    assert SESSION_ID in bundle.session_ids

    # 5. Run judge (mocked)
    transcript = (
        f"Here is my analysis.\n"
        f"{JUDGE_DELIM_START}\n"
        f"{_valid_judge_retro(bundle.bundle_id, SESSION_ID)}\n"
        f"{JUDGE_DELIM_END}\n"
    )
    fake = FakeSubAgentRunner(project, [transcript])
    runner = JudgeRunner(project, subagent_runner=fake, bundle_manager=bm)
    judge_path = runner.judge(bundle_id=bundle.bundle_id)

    # 6. Validate all artifacts
    assert judge_path.exists()
    loaded, body = load(judge_path)
    assert isinstance(loaded, JudgeRetroV1)
    assert loaded.bundle_id == bundle.bundle_id
    assert len(loaded.findings) == 1
    assert loaded.findings[0].evidence[0].session_id == SESSION_ID

    # Verify judge was called once with role=judge
    assert len(fake.calls) == 1
    assert fake.calls[0]["role_name"] == "judge"
