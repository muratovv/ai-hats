"""e2e (HATS-2020)

flow:   an operator aborts a running headless session — kill -TERM on the
        holder's pid from the header, or Ctrl-C in its terminal
cmds:
    ai-hats headless -p claude -r assistant
expect: exit 143 on SIGTERM and 130 on SIGINT; the log still ends with
        run_ended and the session is finalized; no claude process outlives the
        holder
why:    a script must learn the outcome from the exit code and never leave an
        orphaned claude behind — the child runs in its own process group, so
        only the holder can take it down
"""

from __future__ import annotations

import json
import signal
import time

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _wait_for(condition, timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.parametrize(("signum", "code"), [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_e2e_an_aborted_session_is_recorded_and_leaves_no_claude(
    tmp_project, tmp_path, signum: int, code: int
) -> None:
    stub = install(tmp_path)
    session = HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    )
    session.prompt("@sleep 30 a long turn")
    # Positive control for the orphan check below: the same probe sees the stub
    # while the turn runs.
    assert _wait_for(lambda: stub.running()), "the stub never showed up to pgrep"

    end = session.terminate(signum)

    assert end.code == code
    assert end.events[-1]["event"] == "run_ended"
    assert _wait_for(lambda: not stub.running(), 5.0), f"orphans: {stub.running()}"
    metrics = json.loads((session.header.session_dir / "metrics.json").read_text())
    assert metrics["finalized"] is True
