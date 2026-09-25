"""e2e (HATS-2029)

flow:   a headless session is killed outright; someone who did not start it
        looks at the session list, then collects what the session left
cmds:
    ai-hats headless -p claude -r assistant
    kill -KILL <holder_pid>
    ai-hats session list --json
    ai-hats session backfill <session_id>
expect: the list says live while the holder runs and dead after the kill;
        backfill collects the dead session's audit and counters from the
        claude record and leaves events.jsonl byte for byte as the run left it;
        it still refuses a session that is running
why:    a session killed by kill -9 or a reboot never runs its finalize; the
        liveness anchor, not the finalized flag, tells a reader it is over
"""

from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import time

import pytest

from _helpers.headless_client import HeadlessSession
from _helpers.stub_claude import install

pytestmark = [pytest.mark.integration, pytest.mark.observe]

ARGV = ["headless", "-p", "claude", "-r", "assistant"]


def _ai_hats(project, env, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(project.ai_hats_binary), *args],
        cwd=project.path,
        env={**env, "COLUMNS": "250"},  # rich wraps a narrow table's note mid-phrase
        capture_output=True,
        text=True,
        timeout=120,
    )


def _state(project, env, session_id: str) -> str:
    listed = _ai_hats(project, env, "session", "list", "--all", "--json")
    assert listed.returncode == 0, listed.stderr
    return next(s["state"] for s in json.loads(listed.stdout) if s["session_id"] == session_id)


def _wait_for(condition, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_e2e_a_killed_session_reads_dead_and_backfill_collects_it(tmp_project, tmp_path):
    stub = install(tmp_path)
    env = stub.session_env(tmp_project)
    session = HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), *ARGV], cwd=tmp_project.path, env=env
    )
    session_id, session_dir = session.header.session_id, session.header.session_dir
    session.turn("a turn that lands in the record")
    # POSITIVE CONTROL: the anchor is read, and it says live while the holder runs.
    assert _state(tmp_project, env, session_id) == "live"

    session.terminate(signal.SIGKILL)
    assert _wait_for(lambda: not stub.running()), f"orphans: {stub.running()}"

    assert _state(tmp_project, env, session_id) == "dead"
    log = (session_dir / "events.jsonl").read_bytes()
    collected = _ai_hats(tmp_project, env, "session", "backfill", session_id)
    assert collected.returncode == 0, collected.stderr
    assert "owner dead" in collected.stdout, collected.stdout
    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert (metrics["measured"], metrics["finalized"]) == (True, False)
    assert metrics["turns"] >= 1
    assert "## Turn 1" in (session_dir / "audit.md").read_text(), "built from the claude record"
    assert hashlib.sha256((session_dir / "events.jsonl").read_bytes()).digest() == (
        hashlib.sha256(log).digest()
    ), "backfill must leave the log as the run left it"


def test_e2e_backfill_leaves_a_running_session_alone(tmp_project, tmp_path):
    """Negative control: a live owner is not a dead one."""
    stub = install(tmp_path)
    env = stub.session_env(tmp_project)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), *ARGV], cwd=tmp_project.path, env=env
    ) as session:
        session.turn("still running")
        refused = _ai_hats(tmp_project, env, "session", "backfill", session.header.session_id)
        end = session.close()

    assert "still running" in refused.stdout, refused.stdout
    assert end.code == 0
