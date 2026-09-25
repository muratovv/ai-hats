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
    [verdict] = [v for v in turn.of("gate_verdict") if v.get("hook") == "person"]
    assert verdict["decision"] == "deny" and verdict["call_id"] == result["call_id"]
    assert verdict["reason"] == "not today", "the log keeps the person's refusal"


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


def test_e2e_an_answer_the_holder_cannot_execute_is_refused_and_the_question_stays_open(
    session_on_stub,
) -> None:
    with session_on_stub() as session:

        def answer_wrong_then_right(question: dict) -> None:
            session.answer("toolu_nobody_asked", "allow")
            session.answer(question["call_id"], "deny")
            session.answer(question["call_id"], "allow")

        turn = session.turn("@ask", on_question=answer_wrong_then_right)
        end = session.close()

    assert turn.text == "denied", "the first answer on the call decided it"
    refused = [e for e in end.events if e.get("kind") == "command_rejected"]
    assert [e["raw_code"] for e in refused] == ["answer", "answer"]
    assert "names no open question" in refused[0]["detail"]
    assert "already closed" in refused[1]["detail"]


def test_e2e_a_question_open_when_stdin_closes_is_denied_so_the_model_stops(
    session_on_stub, tmp_project
) -> None:
    with session_on_stub() as session:
        id = session.prompt("@ask")
        with pytest.raises(QuestionPending):
            session.turn_for(id, timeout=30)  # the question is open now
        end = session.close()

    [result] = end.of("tool_result_received")
    assert result["ok"] is False and "session is ending" in str(result["content"])
    assert not Path(tmp_project.path, "stub-ran").exists()
    assert end.code == 0


def _asked_about(events, call_id: str) -> list[dict]:
    return [e for e in events if e["event"] == "person_asked" and e["call_id"] == call_id]


def test_e2e_the_models_own_question_is_answered_with_answers(session_on_stub, tmp_project) -> None:
    with session_on_stub() as session:
        turn = session.turn(
            "@askq",
            on_question=lambda q: session.answer(
                q["call_id"], "allow", answers={"Which color?": "Blue"}
            ),
        )
        session.close()

    [call] = [e for e in turn.of("item_emitted") if e["item"]["kind"] == "tool_call"]
    [asked] = _asked_about(turn.events, call["item"]["call_id"])
    assert asked["kind"] == "question", "one person_asked, the reader's"
    assert Path(tmp_project.path, "stub-answers").read_text() == '{"Which color?": "Blue"}'
    assert turn.text == "Blue"


def test_e2e_an_allow_without_answers_leaves_the_model_to_ask_in_words(
    session_on_stub, tmp_project
) -> None:
    with session_on_stub() as session:
        turn = session.turn("@askq", on_question=lambda q: session.answer(q["call_id"], "allow"))
        session.close()

    [result] = turn.of("tool_result_received")
    assert "did not answer" in str(result["content"])
    assert not Path(tmp_project.path, "stub-answers").exists()


@pytest.mark.parametrize(("decision", "text"), [("allow", "planned"), ("deny", "still planning")])
def test_e2e_leaving_plan_mode_is_a_question_like_any_other(
    session_on_stub, decision: str, text: str
) -> None:
    asked: list[dict] = []
    with session_on_stub() as session:

        def decide(question: dict) -> None:
            asked.append(question)
            session.answer(question["call_id"], decision)

        turn = session.turn("@plan", on_question=decide)
        session.close()

    [question] = asked
    assert question["tool"] == "ExitPlanMode" and question["kind"] == "permission"
    assert turn.text == text
