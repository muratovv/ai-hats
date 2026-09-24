"""``commands/v1`` — one stdin line of a headless holder, read strictly.

Strict so a typo is refused out loud instead of lost: a client that wrote
``txt`` for ``text`` hears about it in the log, with the words to fix it.
"""

from __future__ import annotations

import pytest

from ai_hats.headless.commands import Prompt, Rejected, parse_command


@pytest.mark.parametrize(
    "line",
    [
        b'{"v":"commands/v1","cmd":"prompt","text":"hi"}\n',
        b'  {"cmd":"prompt","v":"commands/v1","text":"hi"}  ',
    ],
)
def test_a_prompt_is_read(line: bytes) -> None:
    assert parse_command(line) == Prompt("hi")


@pytest.mark.parametrize("line", [b"\n", b"   \n", b""])
def test_a_blank_line_is_no_command(line: bytes) -> None:
    assert parse_command(line) is None


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
        (
            b'{"v":"commands/v1","cmd":"answer","call_id":"c","decision":"allow"}',
            "answer",
            "not implemented yet",
        ),
        (b'{"v":"commands/v1","cmd":"interrupt"}', "interrupt", "not implemented yet"),
        (b'{"v":"commands/v1","cmd":"stop"}', "stop", 'unknown command "stop"'),
    ],
)
def test_anything_else_is_refused_with_what_to_fix(line: bytes, cmd: str | None, why: str) -> None:
    rejected = parse_command(line)

    assert isinstance(rejected, Rejected)
    assert rejected.cmd == cmd
    assert why in rejected.why


def test_the_refusal_says_what_the_command_takes() -> None:
    rejected = parse_command(b'{"v":"commands/v1","cmd":"prompt","txt":"hi"}')

    assert isinstance(rejected, Rejected)
    assert "prompt takes: text" in rejected.why
