"""E2E: automatic verdict harvest persists into HYP validation_log (HATS-1369).

Drives the REAL ``python -m ai_hats.cli.reflect_session_main <sid>`` subprocess
(the production invocation ``auto_retro._spawn_session_reviewer_background``
uses) against a fixture ``SessionReviewV1`` doc, then reads the HYP back via a
real ``python -m ai_hats_rack context <id> --json`` subprocess and asserts
``validation_log`` grew with the right ``session_id``/``verdict``/``evidence``.

Deterministic, no live claude auth: ``session_id`` is synthesized (no
``sessions/runs/<sid>/`` dir exists for it), so ``compute_facts`` raises
``FileNotFoundError`` before ever reaching the LLM call — wrapped into
``SessionReviewError`` strictly before ``SessionReviewRunner._save()`` (the
only write to the review-doc path), so the fixture doc is never overwritten.

Fail-under-revert: ``git stash`` the harvest changes in
``reflect_session_main.py``/``rack_workspace.py`` -> ``validation_log`` stays
empty after the same run -> this test goes red.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SESSION_ID = "hats1369-e2e-fixture-session"
FIXTURE_EVIDENCE = "e2e fixture: no supporting evidence observed this session"


def _run_reflect_session_main(project_dir: Path, session_id: str) -> subprocess.CompletedProcess[str]:
    """Real subprocess — same argv shape as
    ``auto_retro._spawn_session_reviewer_background`` (production caller)."""
    from _helpers.env import clean_env
    from ai_hats.constants import ENV_SKIP_RETRO

    env = clean_env(os.environ)
    env[ENV_SKIP_RETRO] = "1"
    return subprocess.run(
        [sys.executable, "-m", "ai_hats.cli.reflect_session_main", session_id, "1"],
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _rack(tasks_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Drive the real rack CLI in its own process against THIS checkout — mirrors
    ``tests/e2e/test_rack_append_payload_e2e.py``'s ``_rack`` helper."""
    from _helpers.env import checkout_pythonpath

    env = os.environ.copy()
    env["PYTHONPATH"] = checkout_pythonpath(REPO_ROOT, env.get("PYTHONPATH", ""))
    return subprocess.run(
        [sys.executable, "-m", "ai_hats_rack", *args, "--tasks-dir", str(tasks_dir)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _write_fixture_review_doc(project_dir: Path, session_id: str, hyp_id: str) -> Path:
    """Write a minimal ``SessionReviewV1``-shaped fixture doc directly (mirrors
    ``tests/test_session_review_harness.py``'s ``_make_review_file``) — via
    ``yaml.safe_dump`` so a colon inside ``evidence`` can't break the parse."""
    from ai_hats.paths import retros_dir

    fm = {
        "schema": "hats-session-review/v1",
        "session_id": session_id,
        "summary": "fixture session review for the HATS-1369 harvest e2e",
        "hypothesis_verdicts": [
            {
                "hyp_id": hyp_id,
                "verdict": "refuted",
                "evidence": FIXTURE_EVIDENCE,
                "recommendation": "close_refuted",
            }
        ],
    }
    out = retros_dir(project_dir) / "sessions" / f"{session_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"---\n{yaml.safe_dump(fm)}---\n\n# body\n")
    return out


def test_verdict_harvest_persists_into_hyp_validation_log(tmp_project) -> None:
    from ai_hats.rack_workspace import create_hypothesis, ensure_backlog, rack_workspace

    # Both siblings, mirroring real deployments (HYP/PROP are always mounted as
    # a pair) — main()'s harness-check-failure path files a meta-proposal, which
    # needs the PROP backlog mounted too, or it crashes on an unrelated
    # UnknownPrefixError before ever reaching that unrelated code path.
    for backlog in ("hypotheses", "proposals"):
        ensure_backlog(tmp_project.path, backlog)
    ws = rack_workspace(tmp_project.path)
    hyp_id = create_hypothesis(
        ws, title="HATS-1369 e2e fixture hyp", hypothesis="fixture hypothesis text"
    )

    tasks_dir = tmp_project.path / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"

    before = _rack(tasks_dir, "context", hyp_id, "--json")
    assert before.returncode == 0, before.stderr
    assert not json.loads(before.stdout)["task"].get("validation_log"), (
        f"fixture HYP {hyp_id} should start with no validation_log entries"
    )

    doc_path = _write_fixture_review_doc(tmp_project.path, SESSION_ID, hyp_id)
    before_bytes = doc_path.read_bytes()

    result = _run_reflect_session_main(tmp_project.path, SESSION_ID)

    # Determinism guard: the fixture doc must survive untouched — proves the
    # pipeline failed BEFORE SessionReviewRunner._save() (no LLM call happened),
    # so harvesting below read exactly the fixture we planted, not a real run.
    assert doc_path.read_bytes() == before_bytes, (
        "fixture review doc was overwritten by the subprocess run — "
        "compute_facts must fail (no sessions/runs/<sid>/ dir) before any "
        "LLM call for this test's determinism to hold\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    after = _rack(tasks_dir, "context", hyp_id, "--json")
    assert after.returncode == 0, after.stderr
    log = json.loads(after.stdout)["task"]["validation_log"]

    assert len(log) == 1, (
        f"HATS-1369 regression: expected exactly 1 harvested validation_log "
        f"entry, got: {log}\nreflect_session_main stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    entry = log[0]
    assert entry["session_id"] == SESSION_ID, entry
    assert entry["verdict"] == "refuted", entry
    assert entry["evidence"] == FIXTURE_EVIDENCE, entry
    assert entry["recommendation"] == "close_refuted", entry
