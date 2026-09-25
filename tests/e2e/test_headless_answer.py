"""e2e (HATS-2021)

flow:   a program answers the questions a headless session puts to its stdin
        owner — allow runs the call, deny refuses it — the way a person would
        in the terminal
cmds:
    ai-hats headless -p claude -r assistant
expect: the binary's question reaches the client as person_asked with a call_id;
        answer allow runs the call, answer deny leaves it unrun with the deny's
        message; the turn ends either way
why:    the call a guard or the binary asks about is exactly the one worth
        asking: without an answer a headless session could never run it
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats_client import HeadlessSession, QuestionPending
from ai_hats_client.testing import install

from _helpers.headless import session_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]


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


def test_e2e_an_allowed_call_runs(session_on_stub, tmp_project) -> None:
    asked: list[dict] = []
    with session_on_stub() as session:

        def allow(question: dict) -> None:
            asked.append(question)
            session.answer(question["call_id"], "allow")

        turn = session.turn("@ask", on_question=allow)
        end = session.close()

    [question] = asked
    assert question["event"] == "person_asked" and question["tool"] == "Bash"
    assert Path(tmp_project.path, "stub-ran").read_text() == "echo hi", "the call ran"
    [result] = turn.of("tool_result_received")
    assert result["ok"] is True and result["call_id"] == question["call_id"]
    assert turn.ok and turn.text == "allowed"
    assert end.code == 0


def test_e2e_a_denied_call_does_not_run_and_the_model_hears_why(
    session_on_stub, tmp_project
) -> None:
    with session_on_stub() as session:
        turn = session.turn(
            "@ask",
            on_question=lambda q: session.answer(q["call_id"], "deny", message="not today"),
        )
        session.close()

    assert not Path(tmp_project.path, "stub-ran").exists(), "the call did not run"
    [result] = turn.of("tool_result_received")
    assert result["ok"] is False and "not today" in str(result["content"])
    assert turn.text == "denied"


def test_e2e_a_wait_with_no_handler_stops_at_the_question_instead_of_timing_out(
    session_on_stub,
) -> None:
    with session_on_stub() as session:
        id = session.prompt("@ask")
        with pytest.raises(QuestionPending) as pending:
            session.turn_for(id, timeout=30)
        session.answer(pending.value.question["call_id"], "allow")
        turn = session.turn_for(id, timeout=30)
        session.close()

    assert turn.ok and turn.text == "allowed", "nothing read before the question was lost"
