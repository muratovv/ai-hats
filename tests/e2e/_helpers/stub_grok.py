"""A grok stand-in that speaks a slice of ACP on stdio. Stdlib only."""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> None:
    log_path = os.environ.get("STUB_GROK_LOG")
    hold = os.environ.get("STUB_GROK_HOLD")
    mode = os.environ.get("STUB_GROK_MODE", "turn")
    code = int(os.environ.get("STUB_GROK_EXIT", "0"))
    _log(log_path, {"argv": sys.argv[1:]})
    held = False
    for message in _messages():
        _log(log_path, message)
        method = message.get("method")
        if method == "initialize":
            _send(_initialize(message))
        elif method == "session/new":
            _send(_session_new(message))
        elif method == "session/resume":
            _send(_resumed(message))
        elif method == "session/prompt":
            if hold and not held:
                held = True
                _wait_release(hold)
            _prompt(message, mode)
        elif method is None and "id" in message:
            _log(log_path, {"result_for": message.get("id"), "body": message.get("result")})
    raise SystemExit(code)


def _prompt(message: dict, mode: str) -> None:
    if mode == "permission":
        _send(_permission(message))
        answer = _next()
        _log(os.environ.get("STUB_GROK_LOG"), {"saw": answer})
        _send(_stopped(message, "cancelled" if _rejected(answer) else "end_turn"))
        return
    if mode == "freetext":
        _send(_question(message))
        answer = _next()
        _log(os.environ.get("STUB_GROK_LOG"), {"saw": answer})
        _send(_stopped(message, "end_turn"))
        return
    if mode == "child":
        _send(_spawned(message))
        _send(_permission(message, untrusted="child-from-journal"))
        answer = _next()
        _log(os.environ.get("STUB_GROK_LOG"), {"saw": answer})
        _send(_stopped(message, "end_turn"))
        return
    _send(_commands())
    _send(_user("skill body"))
    text = _prompt_text(message)
    if text:
        _send(_user(text))
    _send(
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": {
                "sessionId": "sess",
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "pong"},
                },
            },
        }
    )
    _send(_stopped(message, "end_turn"))


def _messages():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict):
            yield message


def _next() -> dict:
    for message in _messages():
        return message
    return {}


def _send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _log(path: str | None, message: dict) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(message) + "\n")


def _wait_release(path: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if "release" in open(path, encoding="utf-8").read():
                return
        except OSError:
            pass
        time.sleep(0.05)


def _initialize(request: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True},
            "_meta": {
                "modelState": {
                    "currentModelId": "grok-4.7",
                    "availableModels": [
                        {"modelId": "grok-4.7", "_meta": {"totalContextTokens": 256000}}
                    ],
                }
            },
        },
    }


def _resumed(request: dict) -> dict:
    body = _session_new(request)
    session_id = (request.get("params") or {}).get("sessionId")
    if isinstance(session_id, str) and session_id:
        body["result"]["sessionId"] = session_id
    return body


def _session_new(request: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {"sessionId": "sess", "configOptions": [], "models": {}, "_meta": {}},
    }


def _stopped(request: dict, reason: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "result": {"stopReason": reason, "_meta": {"inputTokens": 10, "sessionId": "sess"}},
    }


def _commands() -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "sess",
            "update": {
                "sessionUpdate": "available_commands_update",
                "availableCommands": [{"name": "/compact"}, {"name": "ship-it"}],
            },
        },
    }


def _user(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "sess",
            "update": {
                "sessionUpdate": "user_message",
                "content": {"type": "text", "text": text},
            },
        },
    }


def _permission(request: dict, untrusted: str | None = None) -> dict:
    tool_meta = {UNTRUSTED: untrusted} if untrusted else {}
    return {
        "jsonrpc": "2.0",
        "id": 50,
        "method": "session/request_permission",
        "params": {
            "sessionId": "sess",
            "toolCall": {
                "toolCallId": "call-1",
                "title": "rm",
                "kind": "execute",
                "rawInput": {"command": "rm"},
                "_meta": tool_meta,
            },
            "options": [
                {"optionId": "allow-once", "kind": "allow_once", "name": "Yes"},
                {"optionId": "always-allow", "kind": "allow_always", "name": "Always"},
                {"optionId": "reject-once", "kind": "reject_once", "name": "No"},
                {"optionId": "reject-always", "kind": "reject_always", "name": "Never"},
            ],
        },
    }


def _question(request: dict) -> dict:
    del request
    return {
        "jsonrpc": "2.0",
        "id": 51,
        "method": "_x.ai/ask_user_question",
        "params": {
            "sessionId": "sess",
            "toolCallId": "q1",
            "questions": [{"question": "Why?", "options": []}],
        },
    }


def _spawned(request: dict) -> dict:
    del request
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": "sess",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "spawn-1",
                "rawOutput": {"subagent_id": "child-from-journal"},
            },
        },
    }


def _rejected(answer: dict) -> bool:
    result = answer.get("result") or {}
    return result.get("optionId") == "reject-once"


def _prompt_text(message: dict) -> str:
    parts = []
    for block in (message.get("params") or {}).get("prompt") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts)


UNTRUSTED = "x.ai/requestingSessionId"


if __name__ == "__main__":
    main()
