"""The claude row of the command table: what the holder writes and what closes a turn."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_hats_observe.canonical import (
    Notice,
    PromptId,
    PromptOrigin,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
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


# --- the stdin owner's prompts, as claude 2.1.283 took them ------------------

FIXTURES = Path(__file__).parent / "fixtures"


def _sent(n: int, text: str) -> Prompt:
    return Prompt(text, PromptId(f"00000000-0000-4000-8000-{n:012x}"))


# five lines written at once: a local command, a refused one, a prompt, /clear, a local command
COMMANDS = (
    _sent(0x1, "/model haiku"),
    _sent(0x2, "/tui"),
    _sent(0x3, "Reply with the single word ok."),
    _sent(0x4, "/clear"),
    _sent(0x5, "/context"),
)
# the second one arrived after the first one's tool call and was folded into its turn
JOINED = (
    _sent(
        0xA1, "Run the shell command `sleep 6` with the Bash tool, then reply with the word done."
    ),
    _sent(0xA2, "Also add the word extra to your reply."),
)


def _replay(fixture: str, prompts: tuple[Prompt, ...]) -> list:
    decoder = ClaudeWire().decoder()
    for prompt in prompts:
        decoder.sent(prompt)
    events = []
    for raw in (FIXTURES / fixture).read_text().splitlines():
        events.extend(decoder.decode(json.loads(raw)))
    return events + decoder.close()


@pytest.mark.parametrize(
    ("fixture", "prompts"), [("commands.wire.jsonl", COMMANDS), ("joined.wire.jsonl", JOINED)]
)
def test_every_prompt_sent_is_received_once_as_the_persons_with_its_text(
    fixture: str, prompts: tuple[Prompt, ...]
) -> None:
    events = _replay(fixture, prompts)

    received = [e for e in events if isinstance(e, PromptReceived)]
    assert [(e.prompt_id, e.text, e.origin) for e in received] == [
        (p.id, p.text, PromptOrigin.PERSON) for p in prompts
    ], "one per prompt, in order, as sent — never claude's <command-name> form"


@pytest.mark.parametrize(
    ("fixture", "prompts"), [("commands.wire.jsonl", COMMANDS), ("joined.wire.jsonl", JOINED)]
)
def test_a_prompt_is_received_before_the_turn_that_answers_it_ends(
    fixture: str, prompts: tuple[Prompt, ...]
) -> None:
    events = _replay(fixture, prompts)

    at = {e.prompt_id: i for i, e in enumerate(events) if isinstance(e, PromptReceived)}
    ends = [(i, e) for i, e in enumerate(events) if isinstance(e, TurnEnded)]
    assert sorted(p for _, e in ends for p in e.prompt_ids) == sorted(p.id for p in prompts)
    for i, ended in ends:
        assert all(at[p] < i for p in ended.prompt_ids)


@pytest.mark.parametrize("n", [0x1, 0x2, 0x5])
def test_a_slash_command_is_received_before_its_answer(n: int) -> None:
    """claude echoes a local command after answering it, and a refused one not at all."""
    events = _replay("commands.wire.jsonl", COMMANDS)
    prompt_id = _sent(n, "").id

    turn = _turn_of(events, prompt_id)
    assert isinstance(turn[0], PromptReceived) and turn[0].prompt_id == prompt_id
    assert any(isinstance(e, ResponseStarted) and e.model == "<synthetic>" for e in turn), (
        "the sample holds the answer the order is about"
    )


def _turn_of(events: list, prompt_id: PromptId) -> list:
    """The events from the previous turn's end to the end of the turn that lists ``prompt_id``."""
    start = 0
    for i, event in enumerate(events):
        if isinstance(event, TurnEnded):
            if prompt_id in event.prompt_ids:
                return events[start:i]
            start = i + 1
    raise AssertionError(f"no turn lists {prompt_id}")


def test_input_the_holder_did_not_send_stays_the_harnesss() -> None:
    decoder = ClaudeWire().decoder()
    decoder.sent(_sent(0x1, "the person's"))
    skill_body = {
        "type": "user",
        "isSynthetic": True,
        "uuid": "3f0e2d9c-0000-4000-8000-0000000000ff",
        "parent_tool_use_id": None,
        "message": {"role": "user", "content": "Base directory for this skill: /x"},
    }

    [received] = decoder.decode(skill_body)

    assert received.origin is PromptOrigin.HARNESS
    assert received.prompt_id == "3f0e2d9c-0000-4000-8000-0000000000ff"


def test_the_binarys_session_is_the_one_its_stdout_last_named() -> None:
    """/clear moves claude to a new session; its own reset line still names the old one."""
    decoder = ClaudeWire().decoder()
    seen = []
    for raw in (FIXTURES / "commands.wire.jsonl").read_text().splitlines():
        line = json.loads(raw)
        decoder.decode(line)
        seen.append((line["type"], decoder.provider_session_id))

    first, moved = "00000000-0000-4000-9000-000000000001", "00000000-0000-4000-9000-000000000002"
    reset = next(i for i, (kind, _) in enumerate(seen) if kind == "conversation_reset")
    assert {sid for _, sid in seen[: reset + 1]} == {first}
    assert seen[reset + 1] == ("system", moved), "the init after the reset names the new one"
    assert {sid for _, sid in seen[reset + 1 :]} == {moved}


def test_a_line_with_no_session_or_a_sub_agents_leaves_it_as_it_was() -> None:
    decoder = ClaudeWire().decoder()
    decoder.decode({"type": "system", "subtype": "init", "session_id": "s-1"})

    decoder.decode({"type": "control_response", "response": {"subtype": "success"}})
    decoder.decode({"type": "assistant", "parent_tool_use_id": "toolu_1", "session_id": "s-2"})

    assert decoder.provider_session_id == "s-1"
