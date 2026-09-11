"""Projections over the event stream.

Kept apart from the domain so a view is always something built *on* the events,
never a shape the events have to accommodate. Every projection here takes an
iterable of events and returns one — they compose, and none of them collects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .events import Event, ItemDelta, ItemEmitted, ResponseEnded, ResponseStarted
from .signals import Blocking, Signal
from .types import Item, ItemKind, ModelName, ResponseId, Usage

# --- selecting items -------------------------------------------------------

EVERYTHING = frozenset(ItemKind)

ANSWER_ONLY = frozenset({ItemKind.TEXT, ItemKind.TOOL_CALL, ItemKind.TOOL_RESULT})
"""For consumers entitled to judge what a run produced but not how it got
there — the reasoning is withheld so it cannot be scored."""

WITH_REASONING = EVERYTHING
"""For comparing two runs, where *how* an answer was reached is the subject."""


def select(events: Iterable[Event], kinds: frozenset[ItemKind]) -> Iterator[Event]:
    """Drop item events outside ``kinds``; pass everything else through.

    Signals and response boundaries always survive: a projection narrows what a
    consumer reads, and must not make a failed run look like a clean one.
    """
    for event in events:
        match event:
            case ItemEmitted() if event.item.kind not in kinds:
                continue
            case ItemDelta() if ItemKind.TEXT not in kinds:
                continue
        yield event


# --- collecting, for consumers that are not live ---------------------------


@dataclass
class Response:
    """A response reassembled from its events — the shape a report wants once a
    run is over."""

    response_id: ResponseId
    model: ModelName | None = None
    items: list[Item] = field(default_factory=list)
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
    for event in events:
        match event:
            case ResponseStarted():
                r = Response(response_id=event.response_id, model=event.model)
                by_id[event.response_id] = r
                out.responses.append(r)
            case ItemEmitted():
                by_id[event.response_id].items.append(event.item)
            case ResponseEnded():
                r = by_id[event.response_id]
                r.usage = event.usage
                r.completion = event.completion
            case _ if isinstance(event, Signal):
                out.signals.append(event)
    return out
