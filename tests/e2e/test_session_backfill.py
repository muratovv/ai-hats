"""e2e: ``ai-hats session backfill`` over a real session dir (HATS-1374).

Real-binary exercise because the provider adapter is wired at mount
(``ai_hats.cli.__init__``) — an in-process test that patches the seam cannot
catch a broken mount. Fails-under-revert: drop the command and the run errors on
"No such command".
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "claude_jsonl" / "three_turns_with_tool.jsonl"

SESSION_ID = "20260730-120000-1"
PROVIDER_SESSION_ID = "e2e05fdc-1374-4aaa-bbbb-000000000001"

UNMEASURED = {
    "schema_version": "audit/v1",
    "finalized": True,
    "role": "maintainer",
    "provider": "claude",
    "exit_code": 0,
    "measured": False,
    "flags": ["no-structured-transcript"],
}


def _seed_session(project_path: Path, *, provider_session_id: str | None) -> Path:
    """Write an unmeasured session record under the project's runs dir."""
    from ai_hats.paths import runs_dir

    session_dir = runs_dir(project_path) / f"session_{SESSION_ID}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metrics = dict(UNMEASURED)
    if provider_session_id:
        metrics["claude_session_id"] = provider_session_id
    (session_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
    return session_dir


def _seed_transcript(project_path: Path, provider_session_id: str) -> Path:
    """Drop the fixture where the claude provider's resolver will look for it."""
    from ai_hats.paths import claude_transcript_path

    target = claude_transcript_path(project_path, provider_session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, target)
    return target


def test_backfill_recovers_counters_from_an_exact_transcript(tmp_project):
    session_dir = _seed_session(tmp_project.path, provider_session_id=PROVIDER_SESSION_ID)
    _seed_transcript(tmp_project.path, PROVIDER_SESSION_ID)

    result = tmp_project.run("session", "backfill", SESSION_ID)
    result.expect_ok()

    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert metrics["measured"] is True, metrics
    assert metrics["turns"] == 2, metrics
    assert metrics["tool_calls"] == 1, metrics
    assert metrics["tokens"]["input"] == 180, metrics


def test_dry_run_writes_nothing(tmp_project):
    session_dir = _seed_session(tmp_project.path, provider_session_id=PROVIDER_SESSION_ID)
    _seed_transcript(tmp_project.path, PROVIDER_SESSION_ID)

    result = tmp_project.run("session", "backfill", SESSION_ID, "--dry-run")
    result.expect_ok()

    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert metrics["measured"] is False, "dry run must not write"
    assert "turns" not in metrics


def test_backfill_refuses_a_session_with_no_recorded_transcript_identity(tmp_project):
    """No ``claude_session_id`` ⇒ no exact match ⇒ refuse.

    Without this the resolver's mtime fallback picks the freshest transcript on
    disk, which is how a first real dry run attributed one identical
    ``turns=5 / tool_calls=216`` to 60 unrelated sessions.
    """
    session_dir = _seed_session(tmp_project.path, provider_session_id=None)
    _seed_transcript(tmp_project.path, PROVIDER_SESSION_ID)

    result = tmp_project.run("session", "backfill", SESSION_ID)
    result.expect_ok()

    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert metrics["measured"] is False, metrics
    assert "turns" not in metrics, "a stranger's transcript must not be attributed"
