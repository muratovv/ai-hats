"""e2e (HATS-2028)

flow:   a headless session's log, taken off the wire, is compared with the
        surface's own record of the same session read after it ended
cmds:
    ai-hats headless -p claude -r assistant
expect: the main agent's events are the same in both, up to the declared
        differences — on the stub (free) and on the live claude binary
        (live_headless, run with `-m live_headless`)
why:    claude has two inputs for one reader, the wire in headless and the
        record in a PTY session; this is what keeps them one reading
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _helpers.env import clean_env
from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env
from _helpers.wire_record_diff import assert_same, from_log, from_record

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


def _compare(session: HeadlessSession, transcript: Path, events: tuple) -> None:
    wire_ids = {e["prompt_id"] for e in events if e.get("event") == "prompt_received"}
    assert_same(from_log(events), from_record(transcript, wire_prompt_ids=wire_ids))


def test_e2e_the_wire_and_the_record_of_a_stub_session_agree(tmp_project, tmp_path) -> None:
    stub = install(tmp_path)
    with HeadlessSession.start(
        [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
        cwd=tmp_project.path,
        env=session_env(stub, tmp_project),
    ) as session:
        session.turn("hello")
        session.turn("@tool")
        session.turn("@error 529 overloaded")
        folded = session.prompt("@fold")
        session.prompt("joined")
        session.turn_for(folded)
        session.turn("@bg")
        session.next_turn()
        end = session.close()

    sid = session.header.provider_session_id
    (transcript,) = stub.config_dir.glob(f"projects/*/{sid}.jsonl")
    _compare(session, transcript, end.events)


@pytest.mark.live_headless
def test_e2e_the_wire_and_the_record_of_a_live_session_agree(
    requires_claude_auth, tmp_project
) -> None:
    (tmp_project.path / "note.txt").write_text("PAPAYA-42\n")
    env = clean_env(os.environ)
    env.update(tmp_project.env)
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    with HeadlessSession.start(
        [
            str(tmp_project.ai_hats_binary),
            "headless",
            "-p",
            "claude",
            "-r",
            "assistant",
            "-m",
            "claude-haiku-4-5",
        ],
        cwd=tmp_project.path,
        env=env,
        timeout=60.0,
    ) as session:
        first = session.turn("Reply with the single word: PAPAYA", timeout=180.0)
        second = session.turn(
            "Use the Read tool to read note.txt, then reply with its content only.",
            timeout=180.0,
        )
        end = session.close(timeout=120.0)

    assert first.ok and second.ok, (first.ended, second.ended)
    sid = session.header.provider_session_id
    (transcript,) = Path.home().glob(f".claude/projects/*/{sid}.jsonl")
    _compare(session, transcript, end.events)
