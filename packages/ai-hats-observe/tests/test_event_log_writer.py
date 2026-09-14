"""``EventLogWriter`` — the session-time writer of ``events.jsonl``.

Every test drives ``tick()`` by hand: the thread ``start()`` spawns only calls
that on an interval, so the contract is provable without sleeping. One test
does start the thread, to prove the interval loop reaches the same tick.

The source is the six-record ``fragments.jsonl`` fixture, fed in whole lines or
torn ones as each test needs — the same record a finished-session reader would
read in one pass, which is what makes the identity test below meaningful.
"""

from __future__ import annotations

import random
import time
from functools import partial
from pathlib import Path

import pytest

from ai_hats_observe.canonical import PromptReceived, ResponseStarted
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events
from ai_hats_observe.event_log_writer import EventLogWriter
from ai_hats_observe.parsers.claude_events import ClaudeTranscriptReader

TRANSCRIPTS = Path(__file__).parent / "fixtures" / "transcripts"
RECORDS = (TRANSCRIPTS / "fragments.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)


def _writer(tmp_path: Path, *sources: Path, **kwargs) -> EventLogWriter:
    """A writer over ``sources`` that only exist once a test has written them —
    the shape of a transcript the surface creates on its first turn."""
    return EventLogWriter(
        locate=lambda: [s for s in sources if s.exists()],
        reader_factory=partial(ClaudeTranscriptReader, live=True),
        path=tmp_path / EVENT_LOG_JSONL,
        **kwargs,
    )


def test_a_source_that_appears_after_the_first_tick_is_followed(tmp_path: Path) -> None:
    """Nothing to locate yet is a quiet tick, not a file; the record is picked
    up on the tick after it appears."""
    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript)

    assert writer.tick() == 0
    assert not log.exists(), "an empty tick must not create the artifact"

    transcript.write_text("".join(RECORDS[:2]), encoding="utf-8")

    assert writer.tick() > 0
    # The fixture opens with the person's prompt, so that is the first event.
    assert isinstance(next(iter(read_events(log))), PromptReceived)


def test_events_appended_between_ticks_arrive_exactly_once(tmp_path: Path) -> None:
    """The reader keeps its offset, so a second tick appends only what the
    surface wrote since the first — a call announced in tick one is never
    announced again in tick two."""
    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript)

    transcript.write_text("".join(RECORDS[:3]), encoding="utf-8")
    first = writer.tick()
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write("".join(RECORDS[3:]))
    second = writer.tick()

    events = list(read_events(log))
    started = [e.response_id for e in events if isinstance(e, ResponseStarted)]
    assert first > 0 and second > 0, "both ticks must have had something to write"
    assert len(events) == first + second
    assert len(started) == len(set(started)), f"a call was announced twice: {started}"
    assert writer.tick() == 0, "a tick with nothing new writes nothing"


def test_a_torn_trailing_line_waits_for_its_newline(tmp_path: Path) -> None:
    """A tick can land while the surface is mid-write. The torn line is not
    parsed (it would read as malformed JSON and cost a false notice); it is
    emitted on the tick after its newline arrives."""
    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript)
    whole, torn_at = RECORDS[1], len(RECORDS[1]) // 2
    assert not (RECORDS[0] + whole[:torn_at]).endswith("\n"), "the tear must land inside a line"

    transcript.write_text(RECORDS[0] + whole[:torn_at], encoding="utf-8")
    from_whole_lines = writer.tick()
    with transcript.open("a", encoding="utf-8") as handle:
        handle.write(whole[torn_at:])
    from_completed_line = writer.tick()

    assert from_whole_lines == 1, "only the prompt line was whole"
    assert from_completed_line > 0
    assert all(type(e).__name__ != "Notice" for e in read_events(log)), "the tear was parsed"


def test_close_drains_what_a_live_reader_held_back(tmp_path: Path) -> None:
    """A live reader cannot know the last call is over until the run is; the
    writer's ``close()`` tells it, and the ending lands in the file."""
    from ai_hats_observe.canonical import ResponseEnded

    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript)
    transcript.write_text("".join(RECORDS), encoding="utf-8")
    writer.tick()

    def ended() -> set[str]:
        return {e.response_id for e in read_events(log) if isinstance(e, ResponseEnded)}

    def started() -> set[str]:
        return {e.response_id for e in read_events(log) if isinstance(e, ResponseStarted)}

    still_open = started() - ended()
    assert len(still_open) == 1, f"the last call must still be open before close: {still_open}"

    outcome = writer.close()

    assert started() == ended(), "close() must end the call the run left open"
    assert outcome.error is None
    assert outcome.events_written == len(list(read_events(log)))


def test_a_source_that_appears_only_at_close_is_still_drained(tmp_path: Path) -> None:
    """A short run can end before the thread ever ticked after the record
    appeared. ``close()`` must adopt the source, then end it, then drain — a
    reader adopted during the drain is never told the run is over and holds
    the last response open for good."""
    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript)
    assert writer.tick() == 0, "the record does not exist yet"
    transcript.write_text("".join(RECORDS), encoding="utf-8")

    outcome = writer.close()

    expected = list(ClaudeTranscriptReader(transcript).read())
    assert list(read_events(log)) == expected
    assert outcome.events_written == len(expected)


# --- identity with a finished-record read -----------------------------------


def _feed_in_chunks(writer: EventLogWriter, transcript: Path, data: bytes, seed: int) -> None:
    """Append ``data`` in random 1–300 byte pieces, ticking after each — every
    write boundary and tick timing the surface and the thread could produce."""
    rng = random.Random(seed)  # noqa: S311 — chunk sizes, not secrets
    position = 0
    with transcript.open("ab") as handle:
        while position < len(data):
            step = rng.randint(1, 300)
            handle.write(data[position : position + step])
            handle.flush()
            position += step
            writer.tick()


@pytest.mark.parametrize("fixture", ["fragments.jsonl", "api_error.jsonl", "normal.jsonl"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_record_fed_in_arbitrary_chunks_reads_back_as_one_pass(
    tmp_path: Path, fixture: str, seed: int
) -> None:
    """The live file IS the record: however the bytes arrived, it equals what
    one reader yields over the finished transcript — the identity HATS-1966
    proved for the post-hoc write, now for the live one."""
    source = TRANSCRIPTS / fixture
    expected = list(ClaudeTranscriptReader(source).read())
    transcript = tmp_path / "t.jsonl"
    writer = _writer(tmp_path, transcript)

    _feed_in_chunks(writer, transcript, source.read_bytes(), seed)
    outcome = writer.close()

    assert list(read_events(tmp_path / EVENT_LOG_JSONL)) == expected
    assert outcome.events_written == len(expected)
    assert outcome.error is None


def test_the_chunk_feed_is_hostile_enough_to_break_a_reader_that_closes_at_eof(
    tmp_path: Path,
) -> None:
    """Positive control for the identity above: the same feed through a reader
    that treats every pause as the end of the record (``live=False``) does NOT
    read back as one pass — so the identity test is exercising torn lines and
    early endings, not passing on a feed too gentle to matter."""
    source = TRANSCRIPTS / "fragments.jsonl"
    expected = list(ClaudeTranscriptReader(source).read())
    transcript = tmp_path / "t.jsonl"
    writer = EventLogWriter(
        locate=lambda: [transcript] if transcript.exists() else [],
        reader_factory=ClaudeTranscriptReader,
        path=tmp_path / EVENT_LOG_JSONL,
    )

    _feed_in_chunks(writer, transcript, source.read_bytes(), seed=1)
    writer.close()

    assert list(read_events(tmp_path / EVENT_LOG_JSONL)) != expected


# --- several sources, faults, the thread --------------------------------------


def test_every_located_source_reaches_the_file(tmp_path: Path) -> None:
    """A surface that rotates its record answers with several paths; each gets
    its own reader and all of them land in the one file."""
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    first.write_text("".join(RECORDS[:3]), encoding="utf-8")
    second.write_text("".join(RECORDS[3:]), encoding="utf-8")
    expected = len(list(ClaudeTranscriptReader(first).read())) + len(
        list(ClaudeTranscriptReader(second).read())
    )
    writer = _writer(tmp_path, first, second)

    writer.tick()
    outcome = writer.close()

    assert outcome.events_written == expected
    assert len(list(read_events(tmp_path / EVENT_LOG_JSONL))) == expected


def _wait_until(condition, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


def test_the_thread_ticks_on_its_own(tmp_path: Path) -> None:
    """``start()`` is the same tick on an interval — the session never calls
    ``tick()`` itself."""
    transcript = tmp_path / "t.jsonl"
    log = tmp_path / EVENT_LOG_JSONL
    writer = _writer(tmp_path, transcript, interval_s=0.005).start()

    transcript.write_text("".join(RECORDS), encoding="utf-8")
    _wait_until(lambda: writer.events_written > 0)
    outcome = writer.close()

    assert writer.events_written > 0, "the thread never ticked"
    assert outcome.error is None
    assert outcome.events_written == len(list(read_events(log)))


def test_a_reader_fault_stops_the_writer_and_is_said_once(tmp_path: Path) -> None:
    """The session must outlive its observer: a reader that raises ends the
    thread, the fault is reported where the session logs, once, and the
    outcome carries it — nothing is swallowed and nothing is retried forever."""

    class Broken:
        exhausted = False

        def read(self):
            raise RuntimeError("boom")

        def close(self) -> None:
            pass

    said: list[str] = []
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(RECORDS[0], encoding="utf-8")
    writer = EventLogWriter(
        locate=lambda: [transcript],
        reader_factory=lambda path: Broken(),
        path=tmp_path / EVENT_LOG_JSONL,
        interval_s=0.005,
        report=said.append,
    ).start()

    _wait_until(lambda: writer.error is not None)
    outcome = writer.close()

    assert outcome.error == "RuntimeError: boom"
    assert said == ["events.jsonl writer stopped: RuntimeError: boom"]
    assert not (tmp_path / EVENT_LOG_JSONL).exists()
