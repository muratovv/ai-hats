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

# A TUI that turns on the kitty keyboard protocol (claude does — `\x1b[>1u`) makes the
# terminal report control keys as CSI-u sequences instead of the legacy control byte,
# so each key is matched in both encodings. `--keys` prints what actually arrives.
DETACH_KEYS: dict[str, tuple[bytes, ...]] = {
    "ctrl-/": (b"\x1f", b"\x1b[47;5u"),
    "ctrl-]": (b"\x1d", b"\x1b[93;5u"),
    "ctrl-\\": (b"\x1c", b"\x1b[92;5u"),
    "ctrl-o": (b"\x0f", b"\x1b[111;5u"),
    "ctrl-g": (b"\x07", b"\x1b[103;5u"),
    "f12": (b"\x1b[24~", b"\x1b[57376u"),
}
# Ctrl-G opens $EDITOR in claude and Ctrl-O toggles its output, so both are taken.
DEFAULT_DETACH = "ctrl-/"

# ai-hats writes its own reset to its stdout, which under a broker goes nowhere. A
# stale keyboard mode makes Enter arrive as \x1b[13u, i.e. a newline instead of submit.
TERM_RESET = b"\x1b[=0;1u\x1b[>4;0m\x1b[20l\x1b>\x1b[?2004l\x1b[?1l\x1b[?25h"
LEAVE_ALT_SCREEN = b"\x1b[?1049l\x1b[?25h\x1b[0m"


def resolve_detach(args) -> tuple[bytes, ...]:
    """Detach trigger: an explicit byte sequence wins over a named key.

    The named table is guesswork about how a terminal encodes a key under whatever
    modes the attached TUI enabled. `--detach-bytes` is the way out of guessing —
    capture the real bytes with `--log-keys`, then name them here.
    """
    if args.detach_bytes:
        try:
            raw = bytes.fromhex(args.detach_bytes.replace(" ", "").replace("0x", ""))
        except ValueError as exc:
            raise SystemExit(f"hats-relay-attach: --detach-bytes is not hex: {exc}") from exc
        if not raw:
            raise SystemExit("hats-relay-attach: --detach-bytes is empty")
        return (raw,)
    return DETACH_KEYS[args.detach_key]


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


async def _pump_stdin(
    ws, stop: asyncio.Future, detach: tuple[bytes, ...], keylog: str | None = None
) -> None:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(0, 65536)
        except (BlockingIOError, OSError):
            return
        if data:
            if keylog:
                # What a key sends depends on the modes the ATTACHED TUI turned on, so
                # this has to be captured in-session; a standalone probe sees a
                # different terminal state and answers the wrong question.
                with contextlib.suppress(OSError), open(keylog, "a") as fh:
                    fh.write(f"{' '.join(f'{b:02x}' for b in data)}  {data!r}\n")
            queue.put_nowait(data)

    loop.add_reader(0, on_readable)
    try:
        while not stop.done():
            data = await queue.get()
            if any(pattern in data for pattern in detach):
                stop.done() or stop.set_result("detached")
                return
            await ws.send(wire.client_input(data))
    finally:
        with contextlib.suppress(Exception):
            loop.remove_reader(0)


async def probe_keys() -> int:
    """Print the raw bytes of each keypress, so a detach key can be chosen on evidence.

    What a key sends depends on the modes the remote TUI turned on, which is why a
    shortcut that looks obvious can silently never match.
    """
    print("Press keys to see what your terminal sends. Ctrl-C to stop.\r")
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def on_readable() -> None:
        data = os.read(0, 65536)
        if data == b"\x03":
            stop.done() or stop.set_result(None)
            return
        pretty = " ".join(f"{b:02x}" for b in data)
        names = [n for n, pats in DETACH_KEYS.items() if any(p in data for p in pats)]
        label = f"  <- matches {', '.join(names)}" if names else ""
        os.write(1, f"  {pretty}   {data!r}{label}\r\n".encode())

    with RawTerminal():
        loop.add_reader(0, on_readable)
        try:
            await stop
        finally:
            with contextlib.suppress(Exception):
                loop.remove_reader(0)
    return 0


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
    if args.keys:
        return await probe_keys()
    if not args.url:
        raise SystemExit("hats-relay-attach: a url is required (or use --keys)")
    if args.list:
        return await list_sessions(args.url)
    async with connect(args.url, max_size=None, compression=None) as ws:
        reply = await _open(ws, args)
        stop: asyncio.Future = asyncio.get_running_loop().create_future()

        detach = resolve_detach(args)
        with RawTerminal():
            os.write(1, TERM_RESET)
            os.write(
                2, f"[relay] session {reply['sid']} — {args.detach_key} to detach\r\n".encode()
            )

            def on_winch(*_a) -> None:
                cols, rows = terminal_size()
                asyncio.ensure_future(ws.send(json.dumps({"op": "resize", "cols": cols, "rows": rows})))

            with contextlib.suppress(ValueError, AttributeError):
                signal.signal(signal.SIGWINCH, on_winch)

            tasks = [
                asyncio.create_task(_pump_stdin(ws, stop, detach, args.log_keys)),
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
    parser.add_argument("url", nargs="?", help="e.g. ws://192.168.1.10:8787")
    parser.add_argument("--role", default="assistant", help="role for a NEW session")
    parser.add_argument("--sid", default=None, help="attach to an existing session instead")
    parser.add_argument("--after-seq", type=int, default=None, help="resume from this sequence")
    parser.add_argument("--list", action="store_true", help="list live sessions and exit")
    parser.add_argument(
        "--detach-key",
        choices=sorted(DETACH_KEYS),
        default=DEFAULT_DETACH,
        help=f"key that detaches without ending the session (default: {DEFAULT_DETACH})",
    )
    parser.add_argument(
        "--detach-bytes",
        default=None,
        help="exact detach sequence as hex (e.g. '1b4f53'); overrides --detach-key",
    )
    parser.add_argument(
        "--keys",
        action="store_true",
        help="print the bytes your terminal sends for each key, then exit (NOT in-session)",
    )
    parser.add_argument(
        "--log-keys",
        default=None,
        metavar="PATH",
        help="while attached, append every keystroke's bytes to PATH — this is the one "
        "that sees what a live TUI's keyboard mode actually produces",
    )
    return parser


def main() -> int:
    return asyncio.run(run_client(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
