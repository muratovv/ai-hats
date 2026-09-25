"""e2e (HATS-2029)

flow:   a headless session ends every way but the plain one — the holder is
        killed outright, its stdout reader goes away, claude ignores the
        holder's SIGTERM, or claude dies from a signal
cmds:
    ai-hats headless -p claude -r assistant
    kill -KILL <holder_pid>
    kill -TERM <holder_pid>
expect: SIGKILL to the holder exits 137 and leaves the log without run_ended;
        a gone reader still lets the session finish and exit 0, recorded; a
        claude that ignores SIGTERM is killed after the grace, exit 143; a
        claude killed by signal 6 makes it exit 134; no claude process outlives
        any of them
why:    a script, CI or HAI learns the outcome from the exit code alone, and
        must never be left with an orphaned claude
"""

from __future__ import annotations

import json
import signal
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.headless_client import HeadlessSession, prompt_command
from _helpers.stub_claude import install

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

ARGV = ["headless", "-p", "claude", "-r", "assistant"]
GRACE_S = 5.0  # ai_hats.headless.runner.GRACE_S


def _wait_for(condition, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _log(session_dir) -> list[dict]:
    return [json.loads(line) for line in (session_dir / "events.jsonl").read_text().splitlines()]


def _start(tmp_project, stub) -> HeadlessSession:
    return HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), *ARGV],
        cwd=tmp_project.path,
        env=stub.session_env(tmp_project),
    )


def test_e2e_a_holder_killed_outright_exits_137_and_leaves_no_claude(tmp_project, tmp_path):
    stub = install(tmp_path)
    session = _start(tmp_project, stub)
    session.prompt("@sleep 2 a turn in flight")
    # POSITIVE CONTROL for the orphan check: the same probe sees the stub now.
    assert _wait_for(lambda: stub.running()), "the stub never showed up to pgrep"

    end = session.terminate(signal.SIGKILL)

    assert end.code == -signal.SIGKILL  # what a shell's $? reads as 137
    assert _wait_for(lambda: not stub.running(), 10.0), f"orphans: {stub.running()}"
    log = _log(session.header.session_dir)
    assert log[0]["event"] == "run_started" and "run_ended" not in {e["event"] for e in log}
    metrics = json.loads((session.header.session_dir / "metrics.json").read_text())
    assert metrics["finalized"] is False


def test_e2e_a_reader_that_goes_away_does_not_end_the_session(tmp_project, tmp_path):
    stub = install(tmp_path)
    proc = subprocess.Popen(
        [str(tmp_project.ai_hats_binary), *ARGV],
        cwd=tmp_project.path,
        env=stub.session_env(tmp_project),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    header = json.loads(proc.stdout.readline())
    proc.stdout.close()  # like `| head -1`: every later write is EPIPE

    proc.stdin.write((prompt_command("still recorded") + "\n").encode())
    proc.stdin.close()
    code = proc.wait(60)

    assert code == 0
    session_dir = Path(header["session_dir"])
    log = _log(session_dir)
    assert log[-1]["event"] == "run_ended" and log[-1]["ok"] is True
    assert any(e["event"] == "turn_ended" for e in log), "the turn ran with no one reading"
    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert metrics["finalized"] is True
    assert not stub.running()


def test_e2e_a_claude_that_ignores_sigterm_is_killed_after_the_grace(tmp_project, tmp_path):
    stub = install(tmp_path)
    session = _start(tmp_project, stub)
    session.prompt("@ignore-term")
    assert _wait_for(lambda: stub.running())
    time.sleep(0.5)  # the stub has reached the turn and ignores SIGTERM from here on

    started = time.monotonic()
    end = session.terminate(signal.SIGTERM, timeout=GRACE_S + 30)

    assert end.code == 143
    assert time.monotonic() - started >= GRACE_S - 0.5, "the holder did not wait out the grace"
    assert end.events[-1]["event"] == "run_ended"
    assert _wait_for(lambda: not stub.running(), 5.0), f"orphans: {stub.running()}"


def test_e2e_a_claude_killed_by_a_signal_is_128_plus_it(tmp_project, tmp_path):
    stub = install(tmp_path)
    session = _start(tmp_project, stub)
    session.prompt("@kill 6")

    end = session.close()

    assert end.code == 134
    assert (end.events[-1]["event"], end.events[-1]["ok"]) == ("run_ended", False)
    assert not stub.running()
