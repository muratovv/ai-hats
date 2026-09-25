"""e2e (HATS-2020)

flow:   a program drives a role's session turn by turn through ai-hats
        headless, the way an editor, a relay or a test framework would
cmds:
    ai-hats headless -p claude -r assistant
expect: each turn ends with turn_ended, and the next prompt goes only after it;
        a turn that failed before the model answered still ends (ok false)
        where it has no response_ended at all; a line the holder cannot run is
        refused in the log and the session goes on
why:    the client learns "my turn is done" from the log alone — a turn with no
        answer, or a typo in a command, must not leave it waiting forever
"""

from __future__ import annotations

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


@pytest.fixture
def session_on_stub(tmp_project, tmp_path):
    """Start a headless session of the assistant role over the stub claude."""
    stub = install(tmp_path)

    def start(*args: str) -> HeadlessSession:
        return HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant", *args],
            cwd=tmp_project.path,
            env=session_env(stub, tmp_project),
        )

    return start


def test_e2e_a_client_holds_a_two_turn_dialogue(session_on_stub) -> None:
    with session_on_stub() as session:
        first = session.turn("remember the word KIWI")
        second = session.turn("@recall")
        end = session.close()

    assert first.ok and second.ok
    assert first.text == "ok: remember the word KIWI"
    assert second.text == "remember the word KIWI", "the second turn saw the first"
    assert [e["event"] for e in second.events if e["event"] != "signal"] == [
        "prompt_received",
        "response_started",
        "item_emitted",
        "response_ended",
        "turn_ended",
    ]
    assert end.code == 0
    assert end.events[-1]["event"] == "run_ended"


def test_e2e_a_turn_that_failed_before_any_answer_still_ends(session_on_stub) -> None:
    """The case turn_ended exists for: an API error leaves no response at all,
    so a client waiting on response_ended would wait forever."""
    with session_on_stub() as session:
        failed = session.turn("@error 529 overloaded")
        after = session.turn("are you there?")
        end = session.close()

    assert not failed.ok
    assert "529 overloaded" in failed.ended["detail"]
    assert failed.of("response_ended") == (), "no response to end — only the turn"
    assert after.ok, "the session goes on after a failed turn"
    assert end.code == 0, "a failed turn is the turn's outcome, not the session's"


def test_e2e_a_turn_with_a_tool_ends_once_after_its_last_answer(session_on_stub) -> None:
    with session_on_stub() as session:
        turn = session.turn("@tool")
        session.close()

    ended = [e["stop_reason"] for e in turn.of("response_ended")]
    assert ended == ["tool_use", "end_turn"], "one response per API call, the turn once"
    assert turn.of("tool_result_received")[0]["ok"] is True
    assert turn.text == "done"


def test_e2e_a_line_the_holder_cannot_run_is_refused_and_the_session_goes_on(
    session_on_stub,
) -> None:
    with session_on_stub() as session:
        session.send_raw("nope")
        session.send_raw('{"v":"commands/v1","cmd":"answer","call_id":"c1","decision":"allow"}')
        turn = session.turn("hi")
        session.close()

    refused = turn.signals("command_rejected")
    assert [(e["raw_code"], e["detail"].split(":")[0]) for e in refused] == [
        (None, "stdin line 1"),
        ("answer", "stdin line 2"),
    ], "each refusal names the line it answers, so the client finds its own command"
    assert "not implemented yet" in refused[1]["detail"]
    assert turn.ok and turn.text == "ok: hi"
