"""The claude row of the command table: what the holder writes and what closes a turn."""

from __future__ import annotations

import json

import pytest

from ai_hats_observe.canonical import ResponseEnded, TurnEnded

from ai_hats.surfaces.claude.provider import ClaudeSurface
from ai_hats.surfaces.claude.wire import ClaudeWire


def test_the_claude_surface_offers_its_wire() -> None:
    assert isinstance(ClaudeSurface().wire(), ClaudeWire)


def test_a_prompt_is_one_user_line_the_binary_reads() -> None:
    line = ClaudeWire().prompt_line("прочитай README")

    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line) == {
        "type": "user",
        "message": {"role": "user", "content": "прочитай README"},
        "parent_tool_use_id": None,
        "session_id": "default",
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
    assert "--permission-prompt-tool" not in args, "no answer command yet to reply with"
