"""The claude row of the command table: what the holder writes and what closes a turn."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ai_hats_observe.canonical import (
    Notice,
    PromptId,
    ResponseEnded,
    ToolCallId,
    TurnEnded,
    WorthRecording,
)
from ai_hats_observe.commands import Answer, Interrupt, Prompt

from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.claude.wire import ClaudeWire
from ai_hats.surfaces.wire import Question, Withdrawn

U1 = "3f0e2d9c-0000-4000-8000-000000000001"


def test_the_claude_surface_offers_its_wire() -> None:
    assert isinstance(ClaudeSurface().wire(), ClaudeWire)


def test_a_prompt_is_one_user_line_the_binary_reads_with_its_id_as_uuid() -> None:
    line = ClaudeWire().encode(Prompt("прочитай README", PromptId(U1)))

    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == {
        "type": "user",
        "message": {"role": "user", "content": "прочитай README"},
        "parent_tool_use_id": None,
        "session_id": "default",
        "uuid": U1,
    }


def test_a_result_line_decodes_to_the_turns_end() -> None:
    events = (
        ClaudeWire()
        .decoder()
        .decode(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "result": "API Error: 529",
                "parent_tool_use_id": None,
            }
        )
    )

    assert [type(e) for e in events] == [TurnEnded]
    assert (events[0].ok, events[0].raw_code, events[0].detail) == (
        False,
        "success",
        "API Error: 529",
    )
    assert events[0].ts, "the moment the result arrived"


def test_a_response_cut_by_the_end_of_stdout_is_ended_at_close() -> None:
    decoder = ClaudeWire().decoder()
    decoder.decode(
        {
            "type": "assistant",
            "request_id": "req_1",
            "parent_tool_use_id": None,
            "message": {"id": "m", "content": [{"type": "text", "text": "half"}]},
        }
    )

    assert [type(e) for e in decoder.close()] == [ResponseEnded]


def test_the_wire_asks_for_the_echo_the_final_usage_and_the_hooks() -> None:
    args = ClaudeWire().launch_args

    for flag in ("--replay-user-messages", "--include-partial-messages", "--include-hook-events"):
        assert flag in args
    assert ClaudeWire().owned_in(list(args[-3:])) == [], "a caller repeating them is harmless"


@pytest.mark.parametrize(
    ("args", "owned"),
    [
        (["--model", "x", "--add-dir", "d"], []),
        (["--input-format", "text"], ["--input-format"]),
        (["--output-format=json"], ["--output-format"]),
        (["--permission-prompt-tool", "stdio"], ["--permission-prompt-tool"]),
        (["--resume", "abc", "-c"], ["--resume", "-c"]),
        (["-p", "hi"], ["-p"]),
        (["--session-id", "x"], ["--session-id"]),
        (["--no-session-persistence"], ["--no-session-persistence"]),
    ],
)
def test_the_flags_that_make_the_wire_are_the_holders(args: list[str], owned: list[str]) -> None:
    assert ClaudeWire().owned_in(args) == owned


def test_the_launch_flags_put_the_binary_on_the_wire_both_ways() -> None:
    args = ClaudeWire().launch_args

    assert args[args.index("--input-format") + 1] == "stream-json"
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args, "every measured wire argv carries it"
    assert args[args.index("--permission-prompt-tool") + 1] == "stdio", "questions come to us"


# As claude 2.1.282 puts a question on the wire.
CAN_USE_TOOL = {
    "type": "control_request",
    "request_id": "req-1",
    "request": {
        "subtype": "can_use_tool",
        "tool_name": "Bash",
        "display_name": "Bash",
        "input": {"command": "TICKET=t git push", "description": "push"},
        "decision_reason": "a gate asks",
        "decision_reason_type": "hook",
        "tool_use_id": "toolu_1",
    },
}
QUESTION = Question(
    request_id="req-1",
    call_id=ToolCallId("toolu_1"),
    tool="Bash",
    input={"command": "TICKET=t git push", "description": "push"},
    reason="a gate asks",
    source="claude/wire",
)


def test_a_question_on_the_wire_is_read_as_one() -> None:
    assert ClaudeWire().decoder().control(CAN_USE_TOOL) == QUESTION


def test_a_question_the_binary_takes_back_is_read_as_withdrawn() -> None:
    line = {"type": "control_cancel_request", "request_id": "req-1"}

    assert ClaudeWire().decoder().control(line) == Withdrawn("req-1")


@pytest.mark.parametrize(
    "line",
    [
        CAN_USE_TOOL,
        {"type": "control_cancel_request", "request_id": "req-1"},
        {"type": "control_response", "response": {"subtype": "success", "request_id": "r"}},
    ],
)
def test_the_binarys_control_lines_are_not_events(line: dict) -> None:
    assert ClaudeWire().decoder().decode(line) == []


def test_a_control_request_nobody_can_answer_is_reported() -> None:
    line = {"type": "control_request", "request_id": "r", "request": {"subtype": "mcp_message"}}
    decoder = ClaudeWire().decoder()

    assert decoder.control(line) is None
    [notice] = decoder.decode(line)
    assert isinstance(notice, Notice) and notice.reason is WorthRecording.UNSUPPORTED_RECORD
    assert notice.raw_code == "control_request:mcp_message"


def _response(line: bytes) -> dict:
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    body = json.loads(line)
    assert body["type"] == "control_response"
    assert body["response"]["subtype"] == "success" and body["response"]["request_id"] == "req-1"
    return body["response"]["response"]


def test_an_allow_runs_the_call_as_it_was_asked_with_what_a_hook_put_on_it() -> None:
    reply = _response(ClaudeWire().reply(QUESTION, Answer(ToolCallId("toolu_1"), "allow")))

    assert reply == {"behavior": "allow", "updatedInput": QUESTION.input}


def test_a_deny_carries_its_message_to_the_model() -> None:
    answer = Answer(ToolCallId("toolu_1"), "deny", "not on master")

    assert _response(ClaudeWire().reply(QUESTION, answer)) == {
        "behavior": "deny",
        "message": "not on master",
    }


def test_a_deny_without_a_message_still_says_who_refused() -> None:
    reply = _response(ClaudeWire().reply(QUESTION, Answer(ToolCallId("toolu_1"), "deny")))

    assert reply["behavior"] == "deny" and reply["message"]


def test_the_models_own_question_is_read_as_one_that_takes_answers() -> None:
    line = {**CAN_USE_TOOL, "request": {**CAN_USE_TOOL["request"], "tool_name": "AskUserQuestion"}}

    assert ClaudeWire().decoder().control(line).takes_answers
    assert not ClaudeWire().decoder().control(CAN_USE_TOOL).takes_answers


def test_answers_to_the_models_questions_ride_on_the_call() -> None:
    asked = replace(QUESTION, tool="AskUserQuestion", takes_answers=True)
    answer = Answer(ToolCallId("toolu_1"), "allow", answers={"Which color?": "Blue"})

    reply = _response(ClaudeWire().reply(asked, answer))

    assert reply["updatedInput"] == {**QUESTION.input, "answers": {"Which color?": "Blue"}}


def test_an_interrupt_is_a_control_request_of_its_own() -> None:
    first, second = (json.loads(ClaudeWire().encode(Interrupt())) for _ in range(2))

    assert first["type"] == "control_request" and first["request"] == {"subtype": "interrupt"}
    assert first["request_id"] != second["request_id"], "the binary answers each by its id"
