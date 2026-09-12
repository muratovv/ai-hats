"""Projections over the event stream.

Kept apart from the domain so a view is always something built *on* the events,
never a shape the events have to accommodate. Every projection here takes an
iterable of events and returns one — they compose, and none of them collects.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .events import (
    Event,
    ItemDelta,
    ItemEmitted,
    ResponseEnded,
    ResponseStarted,
    ToolResultReceived,
)
from .signals import Blocking, Signal
from .types import Item, ItemKind, ModelName, ResponseId, Usage

# --- selecting items -------------------------------------------------------

EVERYTHING = frozenset(ItemKind)

# For consumers entitled to judge what a run produced but not how it got there — the
# reasoning is withheld so it cannot be scored.
ANSWER_ONLY = frozenset({ItemKind.TEXT, ItemKind.TOOL_CALL, ItemKind.TOOL_RESULT})

# For comparing two runs, where *how* an answer was reached is the subject.
WITH_REASONING = EVERYTHING


def select(events: Iterable[Event], kinds: frozenset[ItemKind]) -> Iterator[Event]:
    """Drop item events outside ``kinds``; pass everything else through.

    Signals and response boundaries always survive: a projection narrows what a
    consumer reads, and must not make a failed run look like a clean one.
    """
    for event in events:
        match event:
            case ItemEmitted() if event.item.kind not in kinds:
                continue
            case ToolResultReceived() if ItemKind.TOOL_RESULT not in kinds:
                continue
            case ItemDelta() if ItemKind.TEXT not in kinds:
                continue
        yield event


# --- time order ------------------------------------------------------------

# A reader emits in causal order, which is not time order: a response is closed
# only once something later proves it ended, so its end carries an earlier stamp
# than the tool results that arrived while it was open. The disorder is bounded
# by POSITION, not by time — measured over 693 transcripts an event never
# reaches back more than 2 places, while in seconds it reaches back as far as 12
# days, because a paused session closes its last response whenever it resumes.
# So a buffer a few events deep restores exact time order at no latency, where a
# time-based watermark would have to wait out the pause.
REORDER_DEPTH = 8


def in_time_order(events: Iterable[Event], depth: int = REORDER_DEPTH) -> Iterator[Event]:
    """Re-emit ``events`` ordered by timestamp.

    Holds at most ``depth`` events — four times the worst disorder measured — and
    releases the earliest each time the buffer is full. An event carrying no
    timestamp keeps the position it arrived in, so a source that reports no time
    passes through untouched rather than being flung to the front.
    """
    buffered: list[tuple[str, int, Event]] = []
    watermark = ""
    for seq, event in enumerate(events):
        ts = getattr(event, "ts", None)
        if ts is None:
            ts = watermark
        elif ts > watermark:
            watermark = ts
        heapq.heappush(buffered, (ts, seq, event))
        if len(buffered) > depth:
            yield heapq.heappop(buffered)[2]
    while buffered:
        yield heapq.heappop(buffered)[2]


# --- collecting, for consumers that are not live ---------------------------


@dataclass
class Response:
    """A response reassembled from its events — the shape a report wants once a
    run is over."""

    response_id: ResponseId
    model: ModelName | None = None
    items: list[Item] = field(default_factory=list)
    # answers to this response's calls, which arrive after it has ended
    results: list[ToolResultReceived] = field(default_factory=list)
    usage: Usage = Usage()
    completion: str | None = None

    @property
    def text(self) -> str:
        return "".join(i.text for i in self.items if i.kind is ItemKind.TEXT)


@dataclass
class Collected:
    """A whole run in memory.

    The convenience form for readers that run after the fact — a report, an
    audit file. Live consumers stay on the stream; this is what they fold into
    only when they need the total.
    """

    responses: list[Response] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for r in self.responses:
            total = total + r.usage
        return total

    @property
    def api_calls(self) -> int:
        """Inference calls, which is what cost is proportional to."""
        return len(self.responses)

    @property
    def blocked_by(self) -> Signal | None:
        """The signal that ended the run, if one did."""
        return next((s for s in self.signals if isinstance(s, Blocking)), None)


def collect(events: Iterable[Event]) -> Collected:
    """Fold a stream into ``Collected``. Consumes the stream."""
    out = Collected()
    by_id: dict[ResponseId, Response] = {}
    caller: dict[str, Response] = {}
    for event in events:
        match event:
            case ResponseStarted():
                r = Response(response_id=event.response_id, model=event.model)
                by_id[event.response_id] = r
                out.responses.append(r)
            case ItemEmitted():
                response = by_id[event.response_id]
                response.items.append(event.item)
                if event.item.kind is ItemKind.TOOL_CALL:
                    caller[event.item.call_id] = response
            case ToolResultReceived():
                # attached to the response that asked, which is the tree a
                # report wants even though the stream could not nest it
                owner = caller.get(event.call_id)
                if owner is not None:
                    owner.results.append(event)
            case ResponseEnded():
                r = by_id[event.response_id]
                r.usage = event.usage
                r.completion = event.completion
            case _ if isinstance(event, Signal):
                out.signals.append(event)
    return out
