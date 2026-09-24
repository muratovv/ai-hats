"""The claude row of the command table: what the holder writes and what closes a turn."""

from __future__ import annotations

import json

import pytest

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


def test_a_successful_result_ends_the_turn_with_its_terminal_reason() -> None:
    ended = ClaudeWire().turn_end(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "terminal_reason": "completed",
            "result": "the answer",
        }
    )

    assert ended is not None
    assert (ended.ok, ended.raw_code, ended.detail) == (True, "completed", None)
    assert ended.ts, "the moment the result arrived"


def test_an_error_result_carries_its_text_and_falls_back_to_the_subtype() -> None:
    ended = ClaudeWire().turn_end(
        {"type": "result", "subtype": "success", "is_error": True, "result": "API Error: 529"}
    )

    assert ended is not None
    assert (ended.ok, ended.raw_code, ended.detail) == (False, "success", "API Error: 529")


@pytest.mark.parametrize(
    "line",
    [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {}},
        {"type": "control_response", "response": {}},
        {},
    ],
)
def test_every_other_line_leaves_the_turn_open(line: dict) -> None:
    assert ClaudeWire().turn_end(line) is None


@pytest.mark.parametrize(
    ("args", "owned"),
    [
        (["--model", "x", "--add-dir", "d"], []),
        (["--input-format", "text"], ["--input-format"]),
        (["--output-format=json"], ["--output-format"]),
        (["--permission-prompt-tool", "stdio"], ["--permission-prompt-tool"]),
        (["--resume", "abc", "-c"], ["--resume", "-c"]),
        (["-p", "hi"], ["-p"]),
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
