"""A stand-in `ai-hats headless` for the client's own tests: a header, then one scripted plan.

fake_holder.py <mode> <stdin-log>

ignore-eof   the header, then never exits, stdin closed or not
old-holder   the header of a holder that runs `prompt` alone, then exit at EOF
question     a tool call and a question on it; the turn ends once an answer arrives
withdrawn    a question and its call's result in one write, then the turn ends —
             the holder's log copier writes the lines of one tick so
long-line    a turn whose one text item is longer than a pipe holds
torn         a line cut short by the end of stdout
"""

from __future__ import annotations

import json
import os
import sys
import time


def emit(*bodies: dict) -> None:
    sys.stdout.write("".join(json.dumps({"v": "events/v1", **b}) + "\n" for b in bodies))
    sys.stdout.flush()


def header(commands: list[str]) -> None:
    line = {
        "v": "headless/v1",
        "session_id": "fake",
        "session_dir": "/nonexistent",
        "log": "/nonexistent/events.jsonl",
        "holder_pid": os.getpid(),
        "provider_session_id": "fake",
        "started_at": "2026-09-25T00:00:00Z",
        "events": "events/v1",
        "commands": commands,
    }
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def lines(log: str):
    for raw in sys.stdin:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(raw)
        yield json.loads(raw)


def main(mode: str, log: str) -> int:
    if mode == "ignore-eof":
        header(["prompt", "answer", "interrupt"])
        sys.stdin.close()
        while True:
            time.sleep(1)
    if mode == "old-holder":
        header(["prompt"])
        for _ in lines(log):
            pass
        return 0
    header(["prompt", "answer", "interrupt"])
    if mode == "long-line":
        emit({"event": "item_emitted", "item": {"kind": "text", "text": "x" * 300_000}})
        emit({"event": "turn_ended", "ok": True, "prompt_ids": []})
        for _ in lines(log):
            pass
        return 0
    if mode == "torn":
        sys.stdout.write('{"v": "events/v1", "event": "turn_')
        return 0
    call = {"kind": "tool_call", "call_id": "c1", "name": "AskUserQuestion", "input": {"q": 1}}
    emit({"event": "item_emitted", "item": call})
    asked = {
        "event": "person_asked",
        "kind": "question",
        "call_id": "c1",
        "tool": "AskUserQuestion",
    }
    if mode == "withdrawn":
        emit(asked, {"event": "tool_result_received", "call_id": "c1", "ok": False})
    else:
        emit(asked)
        for command in lines(log):
            if command.get("cmd") == "answer":
                break
        emit({"event": "tool_result_received", "call_id": "c1", "ok": True})
    emit({"event": "turn_ended", "ok": True, "prompt_ids": []})
    for _ in lines(log):
        pass
    emit({"event": "run_ended", "ok": True})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
