"""The registry: many sessions, each fanned out to many clients.

The one rule everything else bends around: **the pump reading a session must never
wait on a client.** ai-hats writes into the seam with a blocking write, and a
socketpair does not share O_NONBLOCK between its ends, so a broker that stops reading
freezes the live agent session. A slow client is therefore evicted, never waited for.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections import deque
from typing import Awaitable, Callable

from .session import Session

logger = logging.getLogger(__name__)

SID_BYTES = 16  # 128 bits: unguessable, so gating on it later needs no protocol change
MAX_QUEUED_BYTES = 4 << 20
RING_BYTES = 1 << 20

# Neither the byte budget nor websockets' ping_timeout catches a peer that simply
# stopped reading; stall duration is the signal that does. See HATS-1193/research.md §3.
STALL_SECONDS = 10.0

Sender = Callable[[bytes], Awaitable[None]]


class Attachment:
    """One client's view of one session: a bounded queue and the task draining it."""

    def __init__(
        self,
        send: Sender,
        on_drop: Callable[[Attachment], None],
        *,
        stall_timeout: float | None = None,
    ) -> None:
        self._send = send
        self._on_drop = on_drop
        self._stall = STALL_SECONDS if stall_timeout is None else stall_timeout
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._queued = 0
        self.dropped = False
        self._task = asyncio.create_task(self._write_loop())

    def offer(self, frame: bytes) -> None:
        """Hand a frame to this client. Never awaits, never raises.

        Over budget means the client cannot keep up; it is dropped rather than
        served a stream with a hole in it, and reconnects to resynchronize.
        """
        if self.dropped:
            return
        if self._queued + len(frame) > MAX_QUEUED_BYTES:
            self.drop()
            return
        self._queued += len(frame)
        self._queue.put_nowait(frame)

    def drop(self) -> None:
        if self.dropped:
            return
        self.dropped = True
        self._queue.put_nowait(None)
        self._on_drop(self)

    async def _write_loop(self) -> None:
        while True:
            frame = await self._queue.get()
            if frame is None:
                return
            self._queued -= len(frame)
            try:
                await asyncio.wait_for(self._send(frame), self._stall)
            except (TimeoutError, asyncio.TimeoutError):
                self.drop()
                return
            except Exception:  # noqa: BLE001 — a dead client must not kill the session
                self.drop()
                return

    async def aclose(self) -> None:
        self.drop()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task


class SessionEntry:
    """A live session plus everyone watching it."""

    def __init__(self, sid: str, session: Session, spec: dict) -> None:
        self.sid = sid
        self.session = session
        self.spec = spec
        self.created_at = time.time()
        self.seq = 0
        self.exited: int | None = None
        self.stdio_tail = b""
        self._ring: deque[tuple[int, bytes]] = deque()
        self._ring_bytes = 0
        self._clients: set[Attachment] = set()
        self.pump_task = asyncio.create_task(self._pump())

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def attach(
        self, send: Sender, *, stall_timeout: float | None = None
    ) -> tuple[Attachment, list[tuple[int, bytes]]]:
        """Attach a client; returns it plus whatever backlog the ring still holds."""
        att = Attachment(send, self._clients.discard, stall_timeout=stall_timeout)
        self._clients.add(att)
        return att, list(self._ring)

    def backlog_since(self, after_seq: int) -> list[tuple[int, bytes]]:
        """Frames the ring can still replay past ``after_seq``."""
        return [(s, d) for s, d in self._ring if s > after_seq]

    def _remember(self, seq: int, data: bytes) -> None:
        self._ring.append((seq, data))
        self._ring_bytes += len(data)
        while self._ring_bytes > RING_BYTES and self._ring:
            _, dropped = self._ring.popleft()
            self._ring_bytes -= len(dropped)

    async def _pump(self) -> None:
        """Drain the session and fan out. Never awaits a client — see the module note."""
        while True:
            data = await self.session.read()
            if data is None:
                break
            self.seq += 1
            self._remember(self.seq, data)
            frame = _encode(self.seq, data)
            for att in list(self._clients):
                att.offer(frame)
        # Reap before reporting: returncode is not populated until the child is waited
        # for, so reading it straight after EOF reports a dead session as running.
        self.exited = await self.session.wait()
        self.stdio_tail = self.session.stdio_tail
        if self.exited:
            logger.warning(
                "session %s exited %s; child said: %s",
                self.sid,
                self.exited,
                self.stdio_tail.decode("utf-8", "replace").strip() or "<nothing>",
            )
        for att in list(self._clients):
            att.drop()

    async def aclose(self) -> None:
        self.pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.pump_task
        for att in list(self._clients):
            await att.aclose()
        await self.session.close()


def _encode(seq: int, data: bytes) -> bytes:
    from . import wire

    return wire.client_output(seq=seq, payload=data)


class Broker:
    """Owns every session and hands out the ids that address them."""

    def __init__(self, argv_for: Callable[[dict], list[str]], *, cwd: str | None = None) -> None:
        self._argv_for = argv_for
        self._cwd = cwd
        self._sessions: dict[str, SessionEntry] = {}

    def get(self, sid: str) -> SessionEntry | None:
        return self._sessions.get(sid)

    def list(self) -> list[dict]:
        return [
            {
                "sid": e.sid,
                "spec": e.spec,
                "created_at": e.created_at,
                "clients": e.client_count,
                "seq": e.seq,
                "exited": e.exited,
            }
            for e in self._sessions.values()
        ]

    async def create(self, spec: dict, *, cols: int, rows: int) -> SessionEntry:
        """Spawn a session and register it under a fresh unguessable id."""
        session = await Session.spawn(
            self._argv_for(spec), cols=cols, rows=rows, cwd=self._cwd
        )
        sid = secrets.token_hex(SID_BYTES)
        entry = SessionEntry(sid, session, spec)
        self._sessions[sid] = entry
        return entry

    async def kill(self, sid: str) -> bool:
        entry = self._sessions.pop(sid, None)
        if entry is None:
            return False
        await entry.aclose()
        return True

    async def aclose(self) -> None:
        for sid in list(self._sessions):
            await self.kill(sid)
