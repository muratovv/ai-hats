"""The session as a stream of append-only events.

A run is read as it happens, so the stream is the interface and any collected
form is a projection built on top of it. Nothing here holds a whole session.

**Every event is emitted exactly once.** An item belongs to one
``ItemEmitted``; a response's cost and outcome arrive once, in ``ResponseEnded``.
Nothing is ever re-announced with a changed status, so a consumer can append
without reconciling and the same item can never appear under two outcomes. A
reader that follows a growing source therefore feeds each record once and
resumes where it stopped — it does not re-read what it has already emitted.

A response still being produced is simply one whose ``ResponseEnded`` has not
arrived. That absence is the only representation of "in flight", which keeps it
from being confused with an outcome the surface actually reported.

What the model asked and what a person asked are separate events. Pairing them
into a dialogue is one possible reading, and belongs to the consumer that wants
it rather than to the shape everyone else must carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterable, AsyncIterator, Iterable, Iterator, Mapping

from .signals import Signal
from .types import Completion, Item, ModelName, ResponseId, Timestamp, Usage


@dataclass(frozen=True)
class PromptReceived:
    """Input addressed to the model, from a person or from the harness."""

    text: str
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ResponseStarted:
    """An inference call began. Its id identifies the call for every event that
    follows, which is what lets cost be attributed to a call rather than to
    however many fragments the surface splits it into."""

    response_id: ResponseId
    model: ModelName | None = None
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ItemEmitted:
    """One complete item of a response."""

    response_id: ResponseId
    item: Item
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ItemDelta:
    """A fragment of an item still being generated, for surfaces that stream
    below item granularity. ``index`` positions it within its response so the
    pieces reassemble in order.

    A surface that reports whole items never emits this; one that streams tokens
    emits deltas followed by the ``ItemEmitted`` that closes the item. Consumers
    that only want finished items ignore this event and stay correct.
    """

    response_id: ResponseId
    index: int
    text: str
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ResponseEnded:
    """An inference call finished, with its outcome and its cost.

    Cost rides the end of the call because that is the one point at which it is
    known once — which is what keeps a call from being billed as many times as
    the surface fragmented it.
    """

    response_id: ResponseId
    completion: Completion
    usage: Usage = Usage()
    stop_reason: str | None = None
    ts: Timestamp | None = None


Event = PromptReceived | ResponseStarted | ItemDelta | ItemEmitted | ResponseEnded | Signal


# --- adapters --------------------------------------------------------------
#
# One per surface. Each is a generator: it yields as it reads and never
# accumulates, so the same adapter serves a finished transcript and a live one.


def read_transcript(records: Iterable[Mapping]) -> Iterator[Event]:
    """Claude session transcript -> events.

    ``records`` is consumed once and lazily; to follow a growing transcript,
    hand it successive slices rather than re-reading from the start.
    """
    raise NotImplementedError("S1")


async def read_stream(messages: AsyncIterable[object]) -> AsyncIterator[Event]:
    """Claude Agent SDK message stream -> the same events.

    Async because the source is; the event vocabulary is identical, so a
    consumer written against one reader works against the other.
    """
    raise NotImplementedError("S4")
