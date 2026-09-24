"""The e2e client writes ``commands/v1`` by hand, stdlib only; it must say what the codec says."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from ai_hats_observe.canonical import PromptId
from ai_hats_observe.commands import Prompt, decode_command, encode_command

_CLIENT = Path(__file__).parent / "e2e" / "_helpers" / "headless_client.py"


def _prompt_line():
    spec = importlib.util.spec_from_file_location("headless_client", _CLIENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)  # its dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module.prompt_line


@pytest.mark.parametrize(
    "prompt",
    [Prompt("прочитай README", PromptId("3f0e2d9c-0000-4000-8000-000000000001")), Prompt("hi")],
)
def test_the_clients_prompt_line_is_the_codecs(prompt: Prompt) -> None:
    line = _prompt_line()(prompt.text, prompt.id)

    assert decode_command(line.encode()) == prompt
    assert json.loads(line) == json.loads(encode_command(prompt))
