"""Registry and fan-out (HATS-1193 S3)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from hats_relay import wire
from hats_relay.broker import RING_BYTES, Broker

FAKE_CHILD = [sys.executable, str(Path(__file__).resolve().parent / "fake_session_child.py")]


def argv_for(spec: dict) -> list[str]:
    return [*FAKE_CHILD, spec["role"]]


def run(coro):
    return asyncio.run(coro)


class Collector:
    """A well-behaved client: takes everything, immediately."""

    def __init__(self) -> None:
        self.frames: list[tuple[int, bytes]] = []

    async def send(self, frame: bytes) -> None:
        self.frames.append(wire.parse_client_output(frame))

    async def wait_for(self, count: int, timeout: float = 10.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.frames) < count:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"got {len(self.frames)} frames, wanted {count}")
            await asyncio.sleep(0.01)

    def payloads(self) -> list[bytes]:
        return [payload for _, payload in self.frames]


def test_session_ids_are_unguessable():
    """Not a counter: gating on the id later must not need a protocol change."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            a = await broker.create({"role": "maintainer"}, cols=100, rows=30)
            b = await broker.create({"role": "maintainer"}, cols=100, rows=30)
            return a.sid, b.sid
        finally:
            await broker.aclose()

    a, b = run(scenario())
    assert a != b
    assert len(a) == 32 and int(a, 16) >= 0


def test_sessions_are_independent():
    """The whole point of the task: M sessions, no crosstalk."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            one = await broker.create({"role": "alpha"}, cols=100, rows=30)
            two = await broker.create({"role": "bravo"}, cols=100, rows=30)
            c1, _ = one.attach(Collector().send)
            watcher = Collector()
            two.attach(watcher.send)

            await one.session.send_input(b"only for one")
            await asyncio.sleep(0.3)
            return watcher.payloads(), [s["spec"]["role"] for s in broker.list()]
        finally:
            await broker.aclose()

    seen_by_other, roles = run(scenario())
    assert b"ONLY FOR ONE" not in b"".join(seen_by_other)
    assert sorted(roles) == ["alpha", "bravo"]


def test_every_attached_client_sees_the_stream():
    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "maintainer"}, cols=100, rows=30)
            first, second = Collector(), Collector()
            entry.attach(first.send)
            entry.attach(second.send)
            await entry.session.send_input(b"shared")
            await first.wait_for(2)
            await second.wait_for(2)
            return first.payloads(), second.payloads()
        finally:
            await broker.aclose()

    first, second = run(scenario())
    assert b"SHARED" in b"".join(first)
    assert first == second


def test_output_is_sequenced_without_gaps():
    """Resume is only possible if the numbering is dense and monotonic."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "maintainer"}, cols=100, rows=30)
            client = Collector()
            entry.attach(client.send)
            for i in range(5):
                await entry.session.send_input(f"msg{i}".encode())
            await client.wait_for(6)
            return [seq for seq, _ in client.frames]
        finally:
            await broker.aclose()

    seqs = run(scenario())
    assert seqs == list(range(1, len(seqs) + 1))


def test_a_stalled_client_stalls_nothing_else():
    """THE invariant. ai-hats writes into the seam with a blocking write and a
    socketpair does not share O_NONBLOCK across its ends, so a broker that stops
    reading freezes the live agent session. A wedged viewer must never do that."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "maintainer"}, cols=100, rows=30)
            healthy = Collector()

            never = asyncio.Event()

            async def wedged(_frame: bytes) -> None:
                await never.wait()

            stalled, _ = entry.attach(wedged)
            entry.attach(healthy.send)

            for i in range(40):
                await entry.session.send_input(f"chunk{i}".encode())
            await healthy.wait_for(41)
            # Read the flag here: teardown drops every attachment by design.
            return healthy.payloads(), entry.seq, stalled.dropped
        finally:
            await broker.aclose()

    payloads, seq, was_dropped = run(scenario())
    assert b"CHUNK39" in b"".join(payloads), "the healthy client fell behind the wedged one"
    assert seq >= 41, "the session pump stopped draining"
    assert not was_dropped, "this load fits the budget; stall-based eviction is S4"


def test_a_wedged_client_is_evicted_on_stall_duration():
    """The byte budget alone never fires: send() backpressure pins the transport
    buffer near its high-water mark, so the queue does not grow enough to trip it.
    Time is the signal that actually distinguishes 'slow' from 'gone'."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "r"}, cols=100, rows=30)
            never = asyncio.Event()

            async def wedged(_frame: bytes) -> None:
                await never.wait()

            stalled, _ = entry.attach(wedged, stall_timeout=0.2)
            await entry.session.send_input(b"one")
            await asyncio.sleep(1.0)
            return stalled.dropped, entry.client_count
        finally:
            await broker.aclose()

    dropped, remaining = run(scenario())
    assert dropped is True
    assert remaining == 0, "an evicted client must also leave the fan-out set"


def test_a_reattaching_client_resumes_exactly_where_it_stopped():
    """Eviction drops the connection, not the bytes: the ring still holds them."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "r"}, cols=100, rows=30)
            early = Collector()
            att, _ = entry.attach(early.send)
            await entry.session.send_input(b"first")
            await early.wait_for(2)
            resume_from = early.frames[-1][0]

            await att.aclose()
            for i in range(3):
                await entry.session.send_input(f"after{i}".encode())
            await asyncio.sleep(0.3)

            missed = entry.backlog_since(resume_from)
            return resume_from, missed, entry.seq
        finally:
            await broker.aclose()

    resume_from, missed, final_seq = run(scenario())
    assert [seq for seq, _ in missed] == list(range(resume_from + 1, final_seq + 1))
    assert b"AFTER2" in b"".join(payload for _, payload in missed)


def test_the_ring_is_bounded():
    """Unbounded history on a session that runs for hours is a memory leak."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "r"}, cols=100, rows=30)
            payload = b"x" * 4096
            for _ in range(400):
                await entry.session.send_input(payload)
            await asyncio.sleep(0.8)
            held = sum(len(d) for _, d in entry.backlog_since(0))
            return held, entry.seq
        finally:
            await broker.aclose()

    held, seq = run(scenario())
    assert seq > 100, "the session should have produced far more than the ring holds"
    assert held <= RING_BYTES


def test_kill_removes_and_reaps():
    async def scenario():
        broker = Broker(argv_for)
        entry = await broker.create({"role": "maintainer"}, cols=100, rows=30)
        sid, pid = entry.sid, entry.session.pid
        killed = await broker.kill(sid)
        await broker.aclose()
        return killed, broker.get(sid), entry.session.returncode, pid

    killed, found, rc, _pid = run(scenario())
    assert killed is True
    assert found is None
    assert rc is not None


def test_spec_reaches_the_child_as_argv():
    """The broker builds argv; the client never supplies one."""

    async def scenario():
        broker = Broker(argv_for)
        try:
            entry = await broker.create({"role": "ai-hats-maintainer"}, cols=100, rows=30)
            client = Collector()
            entry.attach(client.send)
            await client.wait_for(1)
            return json.loads(client.payloads()[0])
        finally:
            await broker.aclose()

    assert run(scenario())["argv"] == ["ai-hats-maintainer"]
