"""``commands/v1`` — one stdin line of a headless holder, read strictly, and written.

Strict so a typo is refused out loud instead of lost: a client that wrote
``txt`` for ``text`` hears about it in the log, with the words to fix it.
"""

from __future__ import annotations

import pytest

from ai_hats_observe.canonical import PromptId, ToolCallId
from ai_hats_observe.commands import (
    Answer,
    Interrupt,
    Prompt,
    Rejected,
    decode_command,
    encode_command,
)


@pytest.mark.parametrize(
    "line",
    [
        b'{"v":"commands/v1","cmd":"prompt","text":"hi"}\n',
        b'  {"cmd":"prompt","v":"commands/v1","text":"hi"}  ',
    ],
)
def test_a_prompt_is_read(line: bytes) -> None:
    assert decode_command(line) == Prompt("hi")


@pytest.mark.parametrize("line", [b"\n", b"   \n", b""])
def test_a_blank_line_is_no_command(line: bytes) -> None:
    assert decode_command(line) is None


@pytest.mark.parametrize(
    ("line", "cmd", "why"),
    [
        (b"nope", None, "not JSON"),
        (b"[1, 2]", None, "not a JSON object"),
        (b"\xff\xfe", None, "not UTF-8"),
        (b'{"cmd":"prompt","text":"hi"}', "prompt", 'missing "v"'),
        (b'{"v":"commands/v2","cmd":"prompt","text":"hi"}', "prompt", 'unsupported "v"'),
        (b'{"v":"commands/v1","text":"hi"}', None, 'missing "cmd"'),
        (b'{"v":"commands/v1","cmd":"prompt","txt":"hi"}', "prompt", 'unknown key "txt"'),
        (b'{"v":"commands/v1","cmd":"prompt","text":""}', "prompt", '"text" must be'),
        (b'{"v":"commands/v1","cmd":"prompt","text":7}', "prompt", '"text" must be'),
        (b'{"v":"commands/v1","cmd":"interrupt","now":true}', "interrupt", "takes no keys"),
        (b'{"v":"commands/v1","cmd":"stop"}', "stop", 'unknown command "stop"'),
    ],
)
def test_anything_else_is_refused_with_what_to_fix(line: bytes, cmd: str | None, why: str) -> None:
    rejected = decode_command(line)

    assert isinstance(rejected, Rejected)
    assert rejected.cmd == cmd
    assert why in rejected.why


def test_the_refusal_says_what_the_command_takes() -> None:
    rejected = decode_command(b'{"v":"commands/v1","cmd":"prompt","txt":"hi"}')

    assert isinstance(rejected, Rejected)
    assert "prompt takes: text, id" in rejected.why


U1 = PromptId("3f0e2d9c-0000-4000-8000-000000000001")


def test_a_prompt_keeps_the_id_its_client_gave_it() -> None:
    line = b'{"v":"commands/v1","cmd":"prompt","id":"%s","text":"hi"}' % U1.encode()

    assert decode_command(line) == Prompt("hi", U1)


@pytest.mark.parametrize(
    "value",
    [
        '"not-a-uuid"',
        '"3F0E2D9C-0000-4000-8000-000000000001"',
        '"3f0e2d9c000040008000000000000001"',
        '"{3f0e2d9c-0000-4000-8000-000000000001}"',
        '""',
        "7",
        "null",
    ],
)
def test_an_id_that_is_not_a_canonical_uuid_is_refused(value: str) -> None:
    """claude takes any string as the record's uuid, so the form is checked here."""
    line = ('{"v":"commands/v1","cmd":"prompt","id":%s,"text":"hi"}' % value).encode()

    rejected = decode_command(line)

    assert isinstance(rejected, Rejected) and rejected.cmd == "prompt"
    assert '"id" must be a UUID' in rejected.why


@pytest.mark.parametrize("prompt", [Prompt("прочитай README", U1), Prompt("no id")])
def test_what_a_client_writes_the_holder_reads_back(prompt: Prompt) -> None:
    line = encode_command(prompt)

    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert decode_command(line) == prompt


def test_a_prompt_without_an_id_is_written_without_one() -> None:
    assert b'"id"' not in encode_command(Prompt("no id"))


def test_an_answer_is_read() -> None:
    line = b'{"v":"commands/v1","cmd":"answer","call_id":"toolu_1","decision":"allow"}'

    assert decode_command(line) == Answer(ToolCallId("toolu_1"), "allow")


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ('"decision":"allow"', '"call_id" must be'),
        ('"call_id":"","decision":"allow"', '"call_id" must be'),
        ('"call_id":"c"', '"decision" must be one of: allow, deny'),
        ('"call_id":"c","decision":"maybe"', '"decision" must be one of'),
        ('"call_id":"c","decision":"allow","message":"why"', '"message" goes with a deny'),
        ('"call_id":"c","decision":"deny","message":3', '"message" must be a string'),
        ('"call_id":"c","decision":"allow","answers":["red"]', '"answers" must be an object'),
        ('"call_id":"c","decision":"allow","answers":{"q":1}', '"answers" must be an object'),
        ('"call_id":"c","decision":"allow","reason":"x"', 'unknown key "reason" — answer takes'),
    ],
)
def test_an_answer_that_cannot_be_executed_is_refused(body: str, why: str) -> None:
    rejected = decode_command(('{"v":"commands/v1","cmd":"answer",%s}' % body).encode())

    assert isinstance(rejected, Rejected) and rejected.cmd == "answer"
    assert why in rejected.why


@pytest.mark.parametrize(
    "answer",
    [
        Answer(ToolCallId("toolu_1"), "allow"),
        Answer(ToolCallId("toolu_1"), "deny", "not on master"),
        Answer(ToolCallId("toolu_1"), "allow", answers={"Which color?": "Blue"}),
    ],
)
def test_what_a_client_answers_the_holder_reads_back(answer: Answer) -> None:
    assert decode_command(encode_command(answer)) == answer


def test_an_interrupt_is_read_and_written_back() -> None:
    assert decode_command(b'{"v":"commands/v1","cmd":"interrupt"}') == Interrupt()
    assert decode_command(encode_command(Interrupt())) == Interrupt()
