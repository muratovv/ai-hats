"""The stub goes on with a conversation under ``--resume <id>``, as claude 2.1.294 does."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ai_hats_client.testing import install

SID = "77777777-8888-4999-8aaa-bbbbbbbbbbbb"
WIRE = ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]


def _turn(stub, cwd: Path, text: str, *args: str) -> list[dict]:
    prompt = {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
        "session_id": "default",
    }
    done = subprocess.run(  # noqa: S603 - the stub under test
        ["claude", *WIRE, *args],
        input=json.dumps(prompt) + "\n",
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **stub.env(os.environ.get("PATH", ""))},
        timeout=30,
        check=True,
    )
    return [json.loads(line) for line in done.stdout.splitlines() if line]


def test_a_resumed_stub_remembers_the_earlier_process_under_the_same_id(tmp_path: Path) -> None:
    stub = install(tmp_path)
    cwd = tmp_path / "proj"
    cwd.mkdir()

    _turn(stub, cwd, "the code word is PINEAPPLE", "--session-id", SID)
    lines = _turn(stub, cwd, "@recall", "--resume", SID)

    (result,) = [line for line in lines if line.get("type") == "result"]
    assert result["result"] == "the code word is PINEAPPLE"
    assert {line.get("session_id") for line in lines if "session_id" in line} == {SID}
    (transcript,) = (stub.config_dir / "projects").glob(f"*/{SID}.jsonl")
    assert transcript.read_text().count('"lastPrompt"') == 2, "one record, both runs"
