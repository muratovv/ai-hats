"""e2e (HATS-2029)

flow:   the holder of a live claude session is killed outright while claude
        runs a tool
cmds:
    ai-hats headless -p claude -r assistant -m claude-haiku-4-5
    kill -KILL <holder_pid>
expect: the real claude leaves by itself once its turn is over — no claude
        process of that session is left behind
why:    the holder cannot take its child down when it is SIGKILLed; ADR-0038
        D5 rests on claude reading EOF and leaving, which only the real binary
        can confirm. Kept out of the gate (live_headless): it spends a turn
"""

from __future__ import annotations

import os
import signal
import subprocess
import time

import pytest

from ai_hats_client import HeadlessSession

from _helpers.env import clean_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces, pytest.mark.live_headless]

MODEL = "claude-haiku-4-5"
TOOL_S = 8


def _claude_of(provider_session_id: str) -> list[int]:
    out = subprocess.run(["pgrep", "-f", provider_session_id], capture_output=True, text=True)
    return [int(pid) for pid in out.stdout.split()]


def _wait_for(condition, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return False


def test_e2e_a_live_claude_leaves_when_its_holder_is_killed(requires_claude_auth, tmp_project):
    env = clean_env(os.environ)
    env.update(tmp_project.env)
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    session = HeadlessSession.start(
        [
            str(tmp_project.ai_hats_binary),
            "headless",
            "-p",
            "claude",
            "-r",
            "assistant",
            "-m",
            MODEL,
        ],
        cwd=tmp_project.path,
        env=env,
        timeout=60.0,
    )
    psid = session.header.provider_session_id
    session.prompt(f"Use the Bash tool to run exactly `sleep {TOOL_S}`, then reply DONE.")
    # POSITIVE CONTROL: the probe finds this session's claude while it runs.
    assert _wait_for(lambda: _claude_of(psid), 30.0), "no claude process found for the session"
    time.sleep(3)  # into the turn, most likely inside the tool

    killed_at = time.monotonic()
    session.terminate(signal.SIGKILL)

    left = _wait_for(lambda: not _claude_of(psid), 90.0)
    assert left, f"claude outlived its holder: {_claude_of(psid)}"
    print(f"claude left {time.monotonic() - killed_at:.1f}s after its holder was killed")
