"""e2e (HATS-2029)

flow:   a script starts a headless session on a machine where claude is not
        logged in
cmds:
    ai-hats headless -p claude -r assistant
expect: exit 1 and nothing on stdout (no header); stderr says to log in; the
        session directory stays, its log holds run_started, the reauthenticate
        signal and run_ended ok false, and metrics.json is finalized with exit 1;
        claude never started a session
why:    headless has no terminal to run claude's login flow, so the refusal
        must come at the start and be recorded, as an interactive session's
        directory would be
"""

from __future__ import annotations

import json

import pytest

from _helpers.headless_client import HeadlessSession, SessionEnded
from _helpers.sessions import snapshot_session_dirs, wait_for_new_session_dir
from _helpers.stub_claude import LOGGED_OUT_ENV, install

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

ARGV = ["headless", "-p", "claude", "-r", "assistant"]


def test_e2e_a_claude_that_is_not_logged_in_is_refused_before_the_start(
    tmp_project, tmp_path
) -> None:
    stub = install(tmp_path)
    env = {**stub.session_env(tmp_project), LOGGED_OUT_ENV: "1"}
    before = snapshot_session_dirs(tmp_project.path)

    with pytest.raises(SessionEnded) as refused:
        HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), *ARGV], cwd=tmp_project.path, env=env
        )

    end = refused.value.exit
    assert end.code == 1 and end.events == ()
    assert "claude auth login" in end.stderr
    session_dir = wait_for_new_session_dir(before, role="assistant", timeout=5.0)
    log = [json.loads(line) for line in (session_dir / "events.jsonl").read_text().splitlines()]
    assert [(e["event"], e.get("kind")) for e in log] == [
        ("run_started", None),
        ("signal", "reauthenticate"),
        ("run_ended", None),
    ]
    assert log[-1]["ok"] is False
    metrics = json.loads((session_dir / "metrics.json").read_text())
    assert (metrics["finalized"], metrics["exit_code"]) == (True, 1)
    assert stub.argvs() == [], "claude never started a session"
    assert stub.running() == []


def test_e2e_a_logged_in_claude_starts_as_usual(tmp_project, tmp_path) -> None:
    """Positive control: the same launch, logged in, gets its header."""
    stub = install(tmp_path)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), *ARGV],
        cwd=tmp_project.path,
        env=stub.session_env(tmp_project),
    ) as session:
        assert session.header.session_id
        end = session.close()

    assert end.code == 0
