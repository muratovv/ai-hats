"""The relay loop: what a client still receives after the child has exited."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import textwrap
import threading
import time

from ai_hats.acp.relay import Relay


def _relay(child: subprocess.Popen[bytes], log_path: str) -> tuple[Relay, int, int]:
    client_r, client_w = os.pipe()
    out_r, out_w = os.pipe()
    os.set_blocking(out_r, False)
    relay = Relay(
        client_in=client_r,
        client_out=out_w,
        child=child,
        event_log=None,
        log_path=log_path,
    )
    return relay, client_w, out_r


def _read_out(fd: int) -> bytes:
    data = b""
    while True:
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            return data
        if not chunk:
            return data
        data += chunk


def test_a_line_already_in_the_pipe_is_delivered(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import json, sys
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}) + "\\n")
        sys.stdout.flush()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    assert child.wait(timeout=5) == 0
    relay, client_w, out_r = _relay(child, str(tmp_path / "log"))
    try:
        relay.run()
        os.close(client_w)
        message = json.loads(_read_out(out_r))
    finally:
        os.close(out_r)
    assert message["result"]["ok"] is True


def test_a_partial_line_does_not_hold_the_relay(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import sys
        sys.stdout.buffer.write(b'{"no":')
        sys.stdout.flush()
        sys.stdin.read()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    relay, client_w, out_r = _relay(child, str(tmp_path / "log"))
    done = threading.Event()

    def _run() -> None:
        relay.run()
        done.set()

    threading.Thread(target=_run, daemon=True).start()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not relay._child_buf:
            time.sleep(0.05)
        assert relay._child_buf
        assert child.stdin is not None
        child.stdin.close()
        assert done.wait(1.5)
    finally:
        os.close(client_w)
        os.close(out_r)
        if child.poll() is None:
            child.kill()


_CHILD = textwrap.dedent(
    """
    import json, sys

    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": 1}})
        elif method == "session/new":
            send({
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {
                    "sessionId": "sess",
                    "configOptions": [{
                        "id": "model",
                        "name": "Model",
                        "category": "model",
                        "type": "select",
                        "currentValue": "grok-4.7",
                        "options": [{"value": "grok-4.7", "name": "Grok"}],
                    }],
                },
            })
        elif method == "session/set_config_option":
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"configOptions": []}})
    """
)


def _send(fd: int, message: dict) -> None:
    os.write(fd, json.dumps(message).encode() + b"\n")


def _until(fd: int, request_id: int) -> dict:
    buf = b""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            if not raw.strip():
                continue
            message = json.loads(raw)
            if message.get("id") == request_id:
                return message
    raise AssertionError(f"no reply {request_id}")


def test_resume_keeps_the_session_id_the_child_omitted(tmp_path) -> None:
    script = textwrap.dedent(
        """
        import json, sys

        def send(message):
            sys.stdout.write(json.dumps(message) + "\\n")
            sys.stdout.flush()

        for line in sys.stdin:
            message = json.loads(line)
            method = message.get("method")
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": 1}})
            elif method == "session/resume":
                send({"jsonrpc": "2.0", "id": message["id"], "result": {"configOptions": []}})
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    client_r, client_w = os.pipe()
    out_r, out_w = os.pipe()
    relay = Relay(
        client_in=client_r,
        client_out=out_w,
        child=child,
        event_log=None,
        log_path=str(tmp_path / "log"),
    )
    threading.Thread(target=relay.run, daemon=True).start()
    try:
        _send(client_w, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        _until(out_r, 1)
        _send(
            client_w,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/resume",
                "params": {"sessionId": "sess-kept", "cwd": "/tmp"},
            },
        )
        resumed = _until(out_r, 2)
    finally:
        os.close(client_w)
        os.close(out_r)
        if child.poll() is None:
            child.kill()
    assert resumed["result"]["sessionId"] == "sess-kept"
    assert resumed["result"]["_meta"]["ai-hats/log"].endswith("log")


def test_setting_plan_keeps_the_childs_options(tmp_path) -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    client_r, client_w = os.pipe()
    out_r, out_w = os.pipe()
    relay = Relay(
        client_in=client_r,
        client_out=out_w,
        child=child,
        event_log=None,
        log_path=str(tmp_path / "log"),
    )
    threading.Thread(target=relay.run, daemon=True).start()
    try:
        _send(client_w, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        _until(out_r, 1)
        _send(
            client_w,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/new",
                "params": {"cwd": "/tmp", "mcpServers": []},
            },
        )
        created = _until(out_r, 2)
        _send(
            client_w,
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "session/set_config_option",
                "params": {"sessionId": "sess", "configId": "plan", "value": "on"},
            },
        )
        answered = _until(out_r, 8)
    finally:
        os.close(client_w)
        os.close(out_r)
        if child.poll() is None:
            child.kill()

    created_ids = [item["id"] for item in created["result"]["configOptions"]]
    answered_ids = [item["id"] for item in answered["result"]["configOptions"]]
    plan = next(item for item in answered["result"]["configOptions"] if item["id"] == "plan")
    assert created_ids == ["model", "plan", "permission_mode"]
    assert answered_ids == ["model", "plan", "permission_mode"]
    assert plan["currentValue"] == "on"
