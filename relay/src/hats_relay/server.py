"""The WebSocket front of the broker.

One connection serves one session: a short control exchange, then the connection
becomes a byte pipe. Binary messages are terminal bytes, text messages are JSON.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from . import protocol, wire
from .broker import Broker, SessionEntry

logger = logging.getLogger(__name__)

# Attaching to a running session shows a blank screen until the TUI next repaints.
# Nudging the width and putting it back drives SIGWINCH, which forces a full repaint —
# verified against a live claude, see tasks/HATS-1193/probe_resize.py.
RESYNC_PAUSE = 0.25


async def _reply_error(ws: ServerConnection, message: str) -> None:
    with contextlib.suppress(ConnectionClosed):
        await ws.send(protocol.error(message))


async def _resync(entry: SessionEntry, cols: int, rows: int) -> None:
    """Force the session to repaint, so a joining client sees the current screen."""
    await entry.session.resize(max(1, cols - 1), rows)
    await asyncio.sleep(RESYNC_PAUSE)
    await entry.session.resize(cols, rows)


async def _pipe(ws: ServerConnection, entry: SessionEntry, ctl: protocol.Control) -> None:
    """Run the byte-pipe phase for one attached client."""
    attachment, _ = entry.attach(ws.send)
    try:
        if ctl.op == "attach":
            for seq, data in entry.backlog_since(ctl.after_seq or 0):
                attachment.offer(wire.client_output(seq=seq, payload=data))
            await _resync(entry, ctl.cols, ctl.rows)

        async for message in ws:
            if isinstance(message, bytes):
                await entry.session.send_input(wire.parse_client_input(message))
                continue
            try:
                inner = protocol.parse_control(message)
            except protocol.ProtocolError as exc:
                await _reply_error(ws, str(exc))
                continue
            if inner.op == "resize":
                await entry.session.resize(inner.cols, inner.rows)
            else:
                await _reply_error(ws, f"op {inner.op!r} is not valid on an attached connection")
    except (ConnectionClosed, ValueError) as exc:
        logger.debug("client left session %s: %s", entry.sid, exc)
    finally:
        await attachment.aclose()


def make_handler(broker: Broker):
    """Build the connection handler bound to ``broker``."""

    async def handler(ws: ServerConnection) -> None:
        try:
            first = await ws.recv()
        except ConnectionClosed:
            return
        if isinstance(first, bytes):
            await _reply_error(ws, "first message must be a JSON control message")
            return

        try:
            ctl = protocol.parse_control(first)
        except protocol.ProtocolError as exc:
            await _reply_error(ws, str(exc))
            return

        if ctl.op == "list":
            with contextlib.suppress(ConnectionClosed):
                await ws.send(protocol.ok(sessions=broker.list()))
            return

        if ctl.op == "kill":
            killed = await broker.kill(ctl.sid)
            with contextlib.suppress(ConnectionClosed):
                await ws.send(protocol.ok(killed=killed) if killed else protocol.error("no such session"))
            return

        if ctl.op == "create":
            try:
                entry = await broker.create(ctl.spec, cols=ctl.cols, rows=ctl.rows)
            except OSError as exc:
                await _reply_error(ws, f"could not start session: {exc}")
                return
        elif ctl.op == "attach":
            entry = broker.get(ctl.sid)
            if entry is None:
                await _reply_error(ws, "no such session")
                return
        else:
            await _reply_error(ws, f"op {ctl.op!r} cannot open a connection")
            return

        with contextlib.suppress(ConnectionClosed):
            await ws.send(protocol.ok(sid=entry.sid, seq=entry.seq))
        await _pipe(ws, entry, ctl)

    return handler


async def shutdown(server, broker: Broker | None = None) -> None:
    """Stop the server without waiting on peers that stopped reading.

    The closing handshake wants to drain, and a peer that is not reading has nowhere
    to drain to; measured at ~19s to stop a server with one such client. Aborting the
    transports first turns that into an immediate stop.
    """
    for connection in list(server.connections):
        with contextlib.suppress(Exception):
            connection.transport.abort()
    server.close()
    await server.wait_closed()
    if broker is not None:
        await broker.aclose()


async def serve_broker(broker: Broker, *, host: str, port: int, **kwargs):
    """Start the server.

    ``host`` is required on purpose: with no authentication, reaching the port is the
    whole right to drive a session, so which interface that is must be a decision
    rather than a default.
    """
    if not host:
        raise ValueError("host is required — bind an explicit interface")
    return await serve(
        make_handler(broker),
        host,
        port,
        # Only an upgrade WITHOUT an Origin is acceptable. Our clients are native or a
        # page we serve; a browser page that is not ours must not be able to drive an
        # agent that has a shell (the CSWSH class — WebSockets are outside the SOP).
        origins=[None],
        # LAN: deflate is GIL-bound and buys nothing here.
        compression=None,
        **kwargs,
    )
