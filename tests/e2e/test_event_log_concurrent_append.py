"""e2e (HATS-1967)

flow:   several hook processes record their verdicts while the session's own
        writer is appending — every producer in its own process, one file
cmds:
    # four processes, each appending 300 events through append_event()
    python -c "from ai_hats_observe.event_log import append_event; ..."
    cat <session_dir>/events.jsonl
expect: exactly 1200 lines, every one decoding to the event its producer
        wrote, payloads intact — including the 20 KB ones
why:    a hook process and the session's writer cannot see each other; the
        promise that their lines never interleave is made to the kernel
        (one write(2) per line under O_APPEND) and only holds across process
        boundaries, which no in-process test can cross
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ai_hats_observe.canonical import ToolResultReceived
from ai_hats_observe.event_log import EVENT_LOG_JSONL, read_events

pytestmark = [pytest.mark.integration, pytest.mark.observe]

_REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCERS = ("a", "b", "c", "d")
PER_PRODUCER = 300
BIG_EVERY = 50
SMALL, BIG = 200, 20_000

PRODUCER_SOURCE = textwrap.dedent(
    """\
    import sys
    from ai_hats_observe.canonical import ToolResultReceived
    from ai_hats_observe.event_log import append_event

    path, tag, count, big_every, small, big = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
    )
    for index in range(count):
        size = big if index % big_every == 0 else small
        append_event(ToolResultReceived(call_id=f"{tag}-{index}", ok=True, content=tag * size), path)
    """
)


def test_producers_in_separate_processes_never_tear_or_merge_a_line(tmp_path: Path) -> None:
    """Four producers, one file — the shape HATS-1967 gave events.jsonl once the
    session's writer and the hook processes started appending at the same time."""
    from _helpers.env import checkout_pythonpath

    log = tmp_path / EVENT_LOG_JSONL
    producer = tmp_path / "producer.py"
    producer.write_text(PRODUCER_SOURCE, encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": checkout_pythonpath(_REPO_ROOT, os.environ.get("PYTHONPATH", "")),
    }

    running = [
        subprocess.Popen(  # noqa: S603 — our own producer script under the test interpreter
            [
                sys.executable,
                str(producer),
                str(log),
                tag,
                str(PER_PRODUCER),
                str(BIG_EVERY),
                str(SMALL),
                str(BIG),
            ],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in PRODUCERS
    ]
    for proc in running:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err

    lines = log.read_bytes().split(b"\n")
    assert lines[-1] == b"", "the file must end on a newline"
    assert len(lines) - 1 == len(PRODUCERS) * PER_PRODUCER, "a line was torn or merged"

    events = list(read_events(log))
    assert len(events) == len(PRODUCERS) * PER_PRODUCER, "a line did not decode"
    assert all(isinstance(e, ToolResultReceived) for e in events)
    assert {e.call_id for e in events} == {
        f"{tag}-{index}" for tag in PRODUCERS for index in range(PER_PRODUCER)
    }
    for event in events:
        tag, index = event.call_id.split("-")
        size = BIG if int(index) % BIG_EVERY == 0 else SMALL
        assert event.content == tag * size, f"{event.call_id}: payload interleaved"
