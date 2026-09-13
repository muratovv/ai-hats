"""The session as a stream of append-only events.

A run is read as it happens, so the stream is the interface and any collected
form is a projection built on top of it. This module is the vocabulary only —
the readers that produce it live in ``reader``.

**Every event is emitted exactly once.** An item belongs to one
``ItemEmitted``; a response's cost and outcome arrive once, in ``ResponseEnded``.
Nothing is ever re-announced with a changed status, so a consumer can append
without reconciling and the same item can never appear under two outcomes. A
reader that follows a growing source therefore feeds each record once and
resumes where it stopped — it does not re-read what it has already emitted.

A response still being produced is simply one whose ``ResponseEnded`` has not
arrived. That absence is the only representation of "in flight", which keeps it
from being confused with an outcome the surface actually reported.

Events arrive in causal order, which is not the same as time order. A response
is closed only once something later proves it ended, so its ``ResponseEnded``
carries an earlier stamp than the tool results that arrived while it was open.
A timestamp always says when something happened, never when this reader worked
it out — so a consumer that only records or displays time, which is every
consumer we have, reads it as the truth about the run and needs nothing else.

The disorder that buys is bounded by POSITION, not by time: measured over 693
transcripts an event never reaches back more than two places, while in seconds
it reaches back as far as twelve days, because a paused session closes its last
response whenever it resumes. A consumer that genuinely needs chronological
order — merging two sources is the one we know of — passes the stream through
``views.in_time_order``, which costs a few events of buffer and no latency.
Ordering is therefore the consumer's choice, not a second timestamp every
call site has to choose between.

What the model asked and what a person asked are separate events. Pairing them
into a dialogue is one possible reading, and belongs to the consumer that wants
it rather than to the shape everyone else must carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar
from .signals import Signal
from .types import (
    Completion,
    Item,
    ItemKind,
    ModelName,
    ResponseId,
    Timestamp,
    ToolCallId,
    Usage,
)


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
    below item granularity.

    A surface that reports whole items never emits this; one that streams tokens
    emits deltas followed by the ``ItemEmitted`` that closes the item. Consumers
    that only want finished items ignore this event and stay correct.
    """

    response_id: ResponseId
    # position within the response, so fragments reassemble in order
    index: int
    text: str
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ToolResultReceived:
    """The harness's answer to a tool call.

    Its own event, parented by the call rather than by the response, because
    the harness produced it and the model did not. That is what lets a response
    end when the model stops talking instead of being held open across a
    round-trip it is not making — so its cost is reported at the right moment
    and no item ever arrives after its response has ended.
    """

    kind: ClassVar[ItemKind] = ItemKind.TOOL_RESULT
    call_id: ToolCallId
    ok: bool
    content: Any = None
    ts: Timestamp | None = None


@dataclass(frozen=True)
class ResponseEnded:
    """An inference call finished, with its outcome and its cost.

    Cost rides the end of the call because that is the one point at which it is
    known once — which is what keeps a call from being billed as many times as
    the surface fragmented it.

    ``ts`` is when the model stopped, not when this reader could tell — which is
    why it can precede events already emitted. See the module docstring.
    """

    response_id: ResponseId
    completion: Completion
    usage: Usage = Usage()
    stop_reason: str | None = None
    ts: Timestamp | None = None


Event = (
    PromptReceived
    | ResponseStarted
    | ItemDelta
    | ItemEmitted
    | ToolResultReceived
    | ResponseEnded
    | Signal
)
