"""e2e (HATS-2058)

flow:   a relay's holder on a live claude conversation is stopped the way a
        redeploy stops it; the next prompt starts a new holder on that
        conversation
cmds:
    ai-hats headless -p claude -r assistant -m claude-haiku-4-5
    kill -TERM <holder_pid>
    ai-hats headless -p claude -r assistant -m claude-haiku-4-5 --resume <provider_session_id>
expect: the resumed session names what the earlier one was told, under the
        same provider_session_id in its header; a fresh session in the same
        directory does not know it
why:    the stub only replays what it is scripted to; whether the real binary
        keeps the context and the id across a resume over the wire is claude's
        own behaviour. Kept out of the gate (live_headless): it spends turns
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import signal

import pytest

from ai_hats_client import HeadlessSession

from _helpers.env import clean_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces, pytest.mark.live_headless]

MODEL = "claude-haiku-4-5"
WORD = "TANGERINE-41"
ASK = "What code word did I give you? Reply with the code word only, or UNKNOWN."


def _session(project, *args: str) -> HeadlessSession:
    env = clean_env(os.environ)
    env.update(project.env)
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    return HeadlessSession.start(
        [str(project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant", "-m", MODEL]
        + list(args),
        cwd=project.path,
        env=env,
        timeout=60.0,
    )


def test_e2e_a_resumed_live_conversation_keeps_its_context_and_its_id(
    requires_claude_auth, tmp_project
) -> None:
    earlier = _session(tmp_project)
    told = earlier.turn(f"Remember this code word: {WORD}. Reply only OK.", timeout=180.0)
    stopped = earlier.terminate(signal.SIGTERM, timeout=60.0)
    sid = earlier.header.provider_session_id

    with _session(tmp_project, "--resume", sid) as resumed:
        recalled = resumed.turn(ASK, timeout=180.0)
        end = resumed.close(timeout=120.0)

    with _session(tmp_project) as fresh:
        unknown = fresh.turn(ASK, timeout=180.0)
        fresh.close(timeout=120.0)

    assert told.ok, told.ended
    assert stopped.code == 143, "stopped as a redeploy stops it"
    assert recalled.ok, recalled.ended
    assert WORD in recalled.text.upper(), recalled.text
    assert resumed.header.provider_session_id == sid
    assert end.code == 0
    # NEGATIVE CONTROL: the word is in the conversation, not in the role or the directory.
    assert WORD not in unknown.text.upper(), unknown.text
    assert fresh.header.provider_session_id != sid
