"""A terminal that attaches to a session.

Keystrokes go up as binary, terminal bytes come down and are written straight to the
local terminal, which does the rendering. Detach with Ctrl-] — the session keeps
running and can be attached again from anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import termios
import tty

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from . import wire

DETACH_KEY = b"\x1d"  # Ctrl-]

# ai-hats writes its own reset to its stdout, which under a broker goes nowhere. A
# stale keyboard mode makes Enter arrive as \x1b[13u, i.e. a newline instead of submit.
TERM_RESET = b"\x1b[=0;1u\x1b[>4;0m\x1b[20l\x1b>\x1b[?2004l\x1b[?1l\x1b[?25h"
LEAVE_ALT_SCREEN = b"\x1b[?1049l\x1b[?25h\x1b[0m"


def terminal_size() -> tuple[int, int]:
    try:
        size = os.get_terminal_size()
        return size.columns, size.lines
    except OSError:
        return 100, 30


class RawTerminal:
    """Put the local terminal in raw mode and always give it back."""

    def __init__(self, fd: int = 0) -> None:
        self._fd = fd
        self._saved = None

    def __enter__(self) -> RawTerminal:
        with contextlib.suppress(termios.error, ValueError):
            self._saved = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        return self

    def __exit__(self, *_exc) -> None:
        if self._saved is not None:
            with contextlib.suppress(termios.error, ValueError):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        with contextlib.suppress(OSError):
            os.write(1, LEAVE_ALT_SCREEN)


async def _open(ws, args) -> dict:
    cols, rows = terminal_size()
    if args.sid:
        request = {"op": "attach", "sid": args.sid, "cols": cols, "rows": rows}
        if args.after_seq is not None:
            request["after_seq"] = args.after_seq
    else:
        request = {"op": "create", "spec": {"role": args.role}, "cols": cols, "rows": rows}
    await ws.send(json.dumps(request))
    reply = json.loads(await ws.recv())
    if "error" in reply:
        raise SystemExit(f"hats-relay: {reply['error']}")
    return reply


async def _pump_stdin(ws, stop: asyncio.Future) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(0, 65536)
        except (BlockingIOError, OSError):
            return
        if data:
            queue.put_nowait(data)

    loop.add_reader(0, on_readable)
    try:
        while not stop.done():
            data = await queue.get()
            if DETACH_KEY in data:
                stop.done() or stop.set_result("detached")
                return
            await ws.send(wire.client_input(data))
    finally:
        with contextlib.suppress(Exception):
            loop.remove_reader(0)


async def _pump_output(ws, stop: asyncio.Future) -> None:
    try:
        async for message in ws:
            if isinstance(message, bytes):
                _, payload = wire.parse_client_output(message)
                os.write(1, payload)
                continue
            reply = json.loads(message)
            if "error" in reply:
                os.write(2, f"\r\n[relay] {reply['error']}\r\n".encode())
    except (ConnectionClosed, ValueError):
        pass
    finally:
        stop.done() or stop.set_result("session ended")


async def list_sessions(url: str) -> int:
    """Print the live sessions, so a human can pick a sid to attach to."""
    async with connect(url, compression=None) as ws:
        await ws.send(json.dumps({"op": "list"}))
        reply = json.loads(await ws.recv())
    sessions = reply.get("sessions", [])
    if not sessions:
        print("no live sessions")
        return 0
    print(f"{'SID':<34}{'ROLE':<24}{'CLIENTS':>8}{'SEQ':>10}  STATE")
    for s in sessions:
        state = "running" if s["exited"] is None else f"exited {s['exited']}"
        print(
            f"{s['sid']:<34}{s['spec'].get('role', '?'):<24}"
            f"{s['clients']:>8}{s['seq']:>10}  {state}"
        )
    return 0


async def run_client(args) -> int:
    if args.list:
        return await list_sessions(args.url)
    async with connect(args.url, max_size=None, compression=None) as ws:
        reply = await _open(ws, args)
        stop: asyncio.Future = asyncio.get_running_loop().create_future()

        with RawTerminal():
            os.write(1, TERM_RESET)
            os.write(2, f"[relay] session {reply['sid']} — Ctrl-] to detach\r\n".encode())

            def on_winch(*_a) -> None:
                cols, rows = terminal_size()
                asyncio.ensure_future(ws.send(json.dumps({"op": "resize", "cols": cols, "rows": rows})))

            with contextlib.suppress(ValueError, AttributeError):
                signal.signal(signal.SIGWINCH, on_winch)

            tasks = [
                asyncio.create_task(_pump_stdin(ws, stop)),
                asyncio.create_task(_pump_output(ws, stop)),
            ]
            reason = await stop
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    print(f"[relay] {reason}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hats-relay-attach", description="Attach a terminal to an ai-hats session."
    )
    parser.add_argument("url", help="e.g. ws://192.168.1.10:8787")
    parser.add_argument("--role", default="assistant", help="role for a NEW session")
    parser.add_argument("--sid", default=None, help="attach to an existing session instead")
    parser.add_argument("--after-seq", type=int, default=None, help="resume from this sequence")
    parser.add_argument("--list", action="store_true", help="list live sessions and exit")
    return parser


def main() -> int:
    return asyncio.run(run_client(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
