"""e2e

flow:   an ACP client runs one grok session through `ai-hats acp`. The child
        is a stand-in that speaks ACP on stdio.
cmds:   ai-hats acp -p grok -r assistant
expect: session/new names the journal; one prompt ends with stopReason; the
        journal has prompt_received origin person; a second prompt waits; the
        process exits with the child's code. A later permission carries the
        child id from the journal and an empty reject records the holder's phrase.
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import time
from pathlib import Path

import pytest

from _helpers.env import clean_env

pytestmark = [pytest.mark.integration, pytest.mark.surfaces]

_STUB = Path(__file__).parent / "_helpers" / "stub_grok.py"


def test_e2e_one_prompt_ends_and_the_next_one_waits(tmp_project, tmp_path: Path) -> None:
    log = tmp_path / "child.jsonl"
    hold = tmp_path / "hold"
    hold.write_text("", encoding="utf-8")
    proc = _start(
        tmp_project,
        tmp_path,
        {"STUB_GROK_LOG": str(log), "STUB_GROK_HOLD": str(hold), "STUB_GROK_EXIT": "3"},
    )
    try:
        _send(proc, _initialize())
        _send(proc, _new())
        created = _until(proc, lambda message: message.get("id") == 2)
        result = created["result"]
        assert result["sessionId"] == "sess"
        journal = Path(result["_meta"]["ai-hats/log"])
        assert journal.name == "events.jsonl"

        _send(proc, _prompt(3, "ping"))
        _wait_for(log, lambda rows: _prompts(rows) == 1)
        _send(proc, _prompt(4, "second"))
        time.sleep(0.4)
        assert _prompts(_rows(log)) == 1, "the second prompt reached the child early"

        hold.write_text("release", encoding="utf-8")
        first = _until(proc, lambda message: message.get("id") == 3)
        assert _saw_usage_before(first)
        assert first["result"]["stopReason"] == "end_turn"
        texts = [
            (message.get("params") or {}).get("update", {}).get("content", {}).get("text")
            for message in first["seen"]
            if (message.get("params") or {}).get("update", {}).get("sessionUpdate")
            == "user_message"
        ]
        assert texts == ["ping"]
        commands = [
            message
            for message in first["seen"]
            if (message.get("params") or {}).get("update", {}).get("sessionUpdate")
            == "available_commands_update"
        ]
        assert commands[-1]["params"]["update"]["_meta"]["ai-hats/skills"] == ["ship-it"]

        second = _until(proc, lambda message: message.get("id") == 4)
        assert second["result"]["stopReason"] == "end_turn"
        assert _prompts(_rows(log)) == 2
    finally:
        _stop(proc)

    assert proc.returncode == 3, proc.stderr.read().decode()[-2000:]
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    prompts = [event for event in events if event.get("event") == "prompt_received"]
    assert [event["text"] for event in prompts] == ["ping", "second"]
    assert {event["origin"] for event in prompts} == {"person"}
    argv = _rows(log)[0]["argv"]
    assert argv[:3] == ["agent", "--no-leader", "stdio"]
    assert "--always-approve" not in argv


def test_e2e_resume_starts_a_journal_without_the_previous_turns(
    tmp_project, tmp_path: Path
) -> None:
    first = _start(tmp_project, tmp_path, {"STUB_GROK_LOG": str(tmp_path / "first.jsonl")})
    try:
        _send(first, _initialize())
        _send(first, _new())
        created = _until(first, lambda message: message.get("id") == 2)
        earlier = Path(created["result"]["_meta"]["ai-hats/log"])
        _send(first, _prompt(3, "ping"))
        _until(first, lambda message: message.get("id") == 3)
    finally:
        _stop(first)
    assert any(
        event.get("text") == "ping"
        for event in _events(earlier)
        if event.get("event") == "prompt_received"
    )

    second = _start(
        tmp_project, tmp_path / "later", {"STUB_GROK_LOG": str(tmp_path / "later.jsonl")}
    )
    try:
        _send(second, _initialize())
        _send(
            second,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/resume",
                "params": {"sessionId": "sess", "cwd": "/tmp"},
            },
        )
        resumed = _until(second, lambda message: message.get("id") == 2)
    finally:
        _stop(second)

    fresh = Path(resumed["result"]["_meta"]["ai-hats/log"])
    assert fresh != earlier
    texts = [
        event.get("text") for event in _events(fresh) if event.get("event") == "prompt_received"
    ]
    assert texts == []


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_e2e_a_child_permission_uses_the_journal_id_and_records_the_phrase(
    tmp_project, tmp_path: Path
) -> None:
    log = tmp_path / "child.jsonl"
    proc = _start(tmp_project, tmp_path, {"STUB_GROK_LOG": str(log), "STUB_GROK_MODE": "child"})
    try:
        _send(proc, _initialize())
        _send(proc, _new())
        created = _until(proc, lambda message: message.get("id") == 2)
        journal = Path(created["result"]["_meta"]["ai-hats/log"])
        _send(proc, _prompt(3, "spawn"))
        found = _until(
            proc,
            lambda message: message.get("method") == "session/request_permission",
        )
        meta = found["params"]["toolCall"]["_meta"]
        assert meta["ai-hats/agent"] == "child-from-journal"
        assert "x.ai/requestingSessionId" not in meta
        kinds = [option["kind"] for option in found["params"]["options"]]
        assert kinds == ["allow_once", "allow_always", "reject_once", "reject_always"]
        _send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": found["id"],
                "result": {"outcome": "selected", "optionId": "reject-once"},
            },
        )
        _wait_for(log, lambda rows: any(row.get("saw") for row in rows))
    finally:
        _stop(proc)

    body = next(row["saw"]["result"] for row in _rows(log) if row.get("saw"))
    assert body == {"outcome": "selected", "optionId": "reject-once"}
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    denied = [event for event in events if event.get("event") == "gate_verdict"]
    assert denied[-1]["reason"] == "The person driving this session denied this call."
    assert denied[-1]["decision"] == "deny"


def _start(project, tmp_path: Path, extra: dict[str, str]) -> subprocess.Popen[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = bin_dir / "grok"
    target.write_text(
        "#!/usr/bin/env python3\n" + _STUB.read_text(encoding="utf-8"), encoding="utf-8"
    )
    target.chmod(0o755)
    env = clean_env(os.environ)
    env.update(project.env)
    env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
    env["XAI_API_KEY"] = "test"
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["GROK_HOME"] = str(tmp_path / "grok-home")
    env.update(extra)
    return subprocess.Popen(
        [str(project.ai_hats_binary), "acp", "-p", "grok", "-r", "assistant"],
        cwd=project.path,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _send(proc: subprocess.Popen[str], message: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()


def _until(proc: subprocess.Popen[str], pred) -> dict:
    """Read until `pred`. The returned message carries the updates seen since the last result."""
    assert proc.stdout is not None
    seen: list[dict] = []
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        if not ready:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                raise AssertionError(
                    f"acp exited {proc.returncode}\n{err[-2000:]}\nseen={seen[-5:]}"
                )
            continue
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read() if proc.stderr else ""
            raise AssertionError(f"stdout closed, exit {proc.wait(timeout=5)}\n{err[-2000:]}")
        message = json.loads(line)
        if pred(message):
            message["seen"] = seen
            return message
        seen.append(message)
    raise AssertionError(f"timed out; last={seen[-5:]}")


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.stdin and not proc.stdin.closed:
        proc.stdin.close()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _prompts(rows: list[dict]) -> int:
    return sum(1 for row in rows if row.get("method") == "session/prompt")


def _wait_for(path: Path, pred) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        rows = _rows(path)
        if rows and pred(rows):
            return
        time.sleep(0.05)
    raise AssertionError(f"stub log never matched: {_rows(path)[-5:]}")


def _saw_usage_before(result: dict) -> bool:
    for message in result["seen"]:
        update = (message.get("params") or {}).get("update") or {}
        if update.get("sessionUpdate") == "usage_update":
            return update.get("used") == 10 and update.get("size") == 256000
    return False


def _initialize() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": 1, "clientCapabilities": {}},
    }


def _new() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "session/new",
        "params": {"cwd": "/tmp", "mcpServers": []},
    }


def _prompt(request_id: int, text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "session/prompt",
        "params": {"sessionId": "sess", "prompt": [{"type": "text", "text": text}]},
    }
