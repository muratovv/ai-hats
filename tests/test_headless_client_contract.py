"""The client writes ``commands/v1`` by hand, stdlib only; it must say what the codec says."""

from __future__ import annotations

import json

import pytest
from ai_hats_client import prompt_command
from ai_hats_observe.canonical import PromptId
from ai_hats_observe.commands import Prompt, decode_command, encode_command


@pytest.mark.parametrize(
    "prompt",
    [Prompt("прочитай README", PromptId("3f0e2d9c-0000-4000-8000-000000000001")), Prompt("hi")],
)
def test_the_clients_prompt_command_is_the_codecs(prompt: Prompt) -> None:
    line = prompt_command(prompt.text, prompt.id)

    assert decode_command(line.encode()) == prompt
    assert json.loads(line) == json.loads(encode_command(prompt))
