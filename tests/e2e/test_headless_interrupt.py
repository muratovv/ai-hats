"""e2e (HATS-2021)

flow:   a program stops a turn that runs too long — mid-answer, mid-tool, or while
        the session waits on its question — and goes on with the same session
cmds:
    ai-hats headless -p claude -r assistant
expect: the cut turn still ends with its turn_ended, soon; a cut tool is not
        recorded as a person's refusal; an open question is closed and a late
        answer to it is refused; the next turn runs as usual
why:    a looping turn used to cost the whole session; the client waits on
        turn_ended, so an interrupt that left no turn end would hang it
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_hats_client import HeadlessSession
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

# Long enough that only an interrupt ends the turn inside the wait below.
RUNS_FOR = 60
WAIT = 20


@pytest.fixture
def session_on_stub(tmp_project, tmp_path):
    """Start a headless session of the assistant role over the stub claude."""
    stub = install(tmp_path)

    def start() -> HeadlessSession:
        return HeadlessSession.start(
            [str(tmp_project.ai_hats_binary), "headless", "-p", "claude", "-r", "assistant"],
            cwd=tmp_project.path,
            env=session_env(stub, tmp_project),
        )

    return start


def _cut(session: HeadlessSession, text: str):
    id = session.prompt(text)
    time.sleep(1.5)  # the turn is running by now
    session.interrupt()
    started = time.monotonic()
    turn = session.turn_for(id, timeout=WAIT)
    return turn, time.monotonic() - started


def test_e2e_a_turn_cut_mid_answer_ends_and_the_session_goes_on(session_on_stub) -> None:
    with session_on_stub() as session:
        turn, took = _cut(session, f"@slow {RUNS_FOR}")
        after = session.turn("are you there?")
        end = session.close()

    assert took < WAIT and not turn.ok
    assert [e["completion"] for e in turn.of("response_ended")] == ["cancelled"]
    assert turn.signals("interrupted"), "the log says a person stopped it"
    assert after.ok and after.text == "ok: are you there?"
    assert end.code == 0


def test_e2e_a_turn_cut_mid_tool_is_not_a_persons_refusal(session_on_stub) -> None:
    with session_on_stub() as session:
        turn, took = _cut(session, f"@slowtool {RUNS_FOR}")
        session.close()

    assert took < WAIT and not turn.ok
    [result] = turn.of("tool_result_received")
    assert result["ok"] is False
    verdicts = [e for e in turn.events if e["event"] == "gate_verdict"]
    assert not [v for v in verdicts if v.get("hook") == "person"], "nobody refused the call"


def test_e2e_without_an_interrupt_the_same_turn_runs_to_its_end(session_on_stub) -> None:
    """Positive control: the slow turn is not what ends early."""
    with session_on_stub() as session:
        turn = session.turn("@slow 1", timeout=WAIT)
        session.close()

    assert turn.ok and turn.text == "partial"
    assert [e["completion"] for e in turn.of("response_ended")] != ["cancelled"]


def test_e2e_an_interrupt_closes_the_open_question_and_a_late_answer_is_refused(
    session_on_stub, tmp_project
) -> None:
    with session_on_stub() as session:
        late: list[str] = []

        def interrupt_instead(question: dict) -> None:
            late.append(question["call_id"])
            session.interrupt()

        turn = session.turn("@ask", timeout=WAIT, on_question=interrupt_instead)
        session.answer(late[0], "allow")
        after = session.turn("hi")
        end = session.close()

    assert not turn.ok and not Path(tmp_project.path, "stub-ran").exists()
    refused = [e for e in end.events if e.get("kind") == "command_rejected"]
    assert [e["raw_code"] for e in refused] == ["answer"]
    assert "already closed" in refused[0]["detail"]
    assert after.ok
