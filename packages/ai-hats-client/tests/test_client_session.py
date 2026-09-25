"""The client against a scripted holder: how it behaves when the session misbehaves."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_hats_client import (
    HeadlessError,
    HeadlessSession,
    HeadlessTimeout,
    QuestionPending,
    answer_command,
    prompt_command,
)

FAKE = Path(__file__).with_name("fake_holder.py")


def _start(mode: str, tmp_path: Path, **kwargs) -> tuple[HeadlessSession, Path]:
    log = tmp_path / "stdin.ndjson"
    session = HeadlessSession.start([sys.executable, str(FAKE), mode, str(log)], **kwargs)
    return session, log


def test_a_with_block_that_outlives_its_close_bound_stops_the_holder(tmp_path: Path) -> None:
    with pytest.raises(HeadlessTimeout):
        with _start("ignore-eof", tmp_path, close_timeout=0.5)[0] as session:
            pass

    assert session._proc.poll() is not None, "no holder left running after the with block"


@pytest.mark.parametrize(
    ("build", "why"),
    [
        (lambda: prompt_command("   "), "text"),
        (lambda: prompt_command("hi", "Turn-1"), "UUID"),
        (lambda: answer_command("c1", "Allow"), "decision"),
        (lambda: answer_command("c1", "allow", message="why"), "message"),
        (lambda: answer_command("c1", "allow", answers={"q": 1}), "answers"),
        (lambda: answer_command("", "allow"), "call_id"),
    ],
)
def test_a_command_the_holder_would_refuse_is_refused_before_it_is_sent(build, why: str) -> None:
    with pytest.raises(ValueError, match=why):
        build()


def test_a_command_the_holder_does_not_run_is_refused_by_name(tmp_path: Path) -> None:
    session, _ = _start("old-holder", tmp_path)
    with session:
        with pytest.raises(HeadlessError, match="answer"):
            session.answer("c1", "allow")
        with pytest.raises(HeadlessError, match="interrupt"):
            session.interrupt()


def test_a_command_after_the_session_ended_is_a_headless_error(tmp_path: Path) -> None:
    session, _ = _start("old-holder", tmp_path)
    session.close(timeout=10)

    with pytest.raises(HeadlessError):
        session.prompt("too late")


def test_a_question_whose_handler_failed_is_offered_again(tmp_path: Path) -> None:
    session, log = _start("question", tmp_path)

    def broken(question: dict) -> None:
        raise RuntimeError("the handler broke")

    with session:
        with pytest.raises(RuntimeError):
            session.next_turn(timeout=10, on_question=broken)
        seen: list[str] = []

        def allow(question: dict) -> None:
            seen.append(question["call_id"])
            session.answer(question["call_id"], "allow", answers={"q": "a"})

        turn = session.next_turn(timeout=10, on_question=allow)

    assert seen == ["c1"] and turn.ok
    assert '"cmd": "answer"' in log.read_text()


def test_a_question_left_unanswered_stops_the_next_wait_again(tmp_path: Path) -> None:
    session, _ = _start("question", tmp_path)
    with session:
        with pytest.raises(QuestionPending):
            session.next_turn(timeout=10)
        with pytest.raises(QuestionPending) as again:
            session.next_turn(timeout=10)
        session.answer(again.value.question["call_id"], "deny")
        turn = session.next_turn(timeout=10)

    assert turn.ok


def test_a_question_closed_by_its_result_is_not_offered(tmp_path: Path) -> None:
    """Its result was read before the wait got to offer it: nobody is being asked."""
    session, _ = _start("withdrawn", tmp_path)
    with session:
        turn = session.next_turn(timeout=10)

    assert turn.of("person_asked") and turn.of("tool_result_received")


def test_the_call_a_question_is_about_is_at_hand(tmp_path: Path) -> None:
    session, _ = _start("question", tmp_path)
    with session:
        with pytest.raises(QuestionPending) as pending:
            session.next_turn(timeout=10)
        call = session.tool_call(pending.value.question["call_id"])
        session.answer("c1", "deny")

    assert call == {
        "kind": "tool_call",
        "call_id": "c1",
        "name": "AskUserQuestion",
        "input": {"q": 1},
    }
    assert session.tool_call("nope") is None
