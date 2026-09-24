"""e2e (HATS-2028)

flow:   a program drives a headless session whose surface writes its record
        late, or is sent the next prompt before the running turn ends
cmds:
    ai-hats headless -p claude -r assistant
expect: every event of a turn is on stdout before that turn's turn_ended, even
        when the surface's record lands after the wire's result; a prompt sent
        ahead is received after the previous turn_ended; each prompt is
        received once
why:    the main agent's events come from the wire alone, so the log's order is
        the wire's by construction — a client that cuts the log at turn_ended
        never loses an answer to the next turn
"""

from __future__ import annotations

import pytest

from _helpers.headless_client import HeadlessSession
from _helpers.stub_claude import install

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


@pytest.fixture
def stub(tmp_path):
    return install(tmp_path)


@pytest.fixture
def session_on_stub(tmp_project, stub):
    def start() -> HeadlessSession:
        return HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
            cwd=tmp_project.path,
            env=stub.session_env(tmp_project),
        )

    return start


def test_e2e_a_turns_answer_precedes_its_end_when_the_record_trails_the_wire(
    session_on_stub, stub
) -> None:
    with session_on_stub() as session:
        turn = session.turn("@late 0.5 hello")
        end = session.close()

    assert turn.text == "ok: hello", "the answer is inside its turn, not after turn_ended"
    assert [e["event"] for e in turn.events if e["event"] not in ("signal", "run_started")] == [
        "prompt_received",
        "response_started",
        "item_emitted",
        "response_ended",
        "turn_ended",
    ]
    (timing,) = stub.timings()
    assert timing["record"] - timing["result"] >= 0.4, "the record really trailed the wire"
    assert len(end.of("prompt_received")) == 1, "the wire alone speaks for the main agent"


def test_e2e_a_prompt_sent_ahead_is_received_after_the_previous_turn_ends(
    session_on_stub,
) -> None:
    with session_on_stub() as session:
        session.prompt("one")
        session.prompt("two")
        first = session.next_turn()
        second = session.next_turn()
        end = session.close()

    assert first.text == "ok: one" and second.text == "ok: two"
    assert [e["text"] for e in first.of("prompt_received")] == ["one"]
    assert [e["text"] for e in second.of("prompt_received")] == ["two"]
    assert end.code == 0
