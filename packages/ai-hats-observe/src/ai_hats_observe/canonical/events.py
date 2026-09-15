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

Every event says whose work it is: ``agent`` names the sub-agent that produced
it and is absent for the main agent. A child's record is read beside its
parent's into the same stream, so without the field a child's calls would count
as the parent's — measured in one fan-out session, they outnumbered them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar
from .signals import Signal
from .types import (
    AgentId,
    AskKind,
    Completion,
    GateDecision,
    GatePoint,
    Item,
    ItemKind,
    ModelName,
    PromptOrigin,
    ResponseId,
    Timestamp,
    ToolCallId,
    Usage,
)


@dataclass(frozen=True)
class RunStarted:
    """This session began observing a run.

    Ours to say, not the surface's: no surface we read persists a start a
    follower can trust, and the one moment that is true for every surface is
    the moment before it is launched. So the writer says it.
    """

    ts: Timestamp | None = None
    agent: AgentId | None = None


@dataclass(frozen=True)
class RunEnded:
    """The run is over — the one thing silence cannot tell a follower.

    A file that stops growing is idle, inside a long tool, or waiting on a
    person; only this line says it ended. Said by the writer after the surface
    exits and its record is drained, even when the follow faulted earlier: a
    fault costs the events after it, never the ending. ``raw_code`` is the
    surface's own word for how, kept verbatim as a signal keeps it. Cost is not
    here — per-response usage already sums to the run.
    """

    ok: bool
    raw_code: str | None = None
    # what stopped the follow early, when something did
    detail: str | None = None
    ts: Timestamp | None = None
    agent: AgentId | None = None


@dataclass(frozen=True)
class PromptReceived:
    """Input addressed to the model, from a person or from the harness.

    ``origin`` says which, when the surface says; a controller waiting for the
    person to come back reads it rather than counting prompts.
    """

    text: str
    ts: Timestamp | None = None
    agent: AgentId | None = None
    origin: PromptOrigin | None = None


@dataclass(frozen=True)
class ResponseStarted:
    """An inference call began. Its id identifies the call for every event that
    follows, which is what lets cost be attributed to a call rather than to
    however many fragments the surface splits it into."""

    response_id: ResponseId
    model: ModelName | None = None
    ts: Timestamp | None = None
    agent: AgentId | None = None


@dataclass(frozen=True)
class ItemEmitted:
    """One complete item of a response."""

    response_id: ResponseId
    item: Item
    ts: Timestamp | None = None
    agent: AgentId | None = None


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
    agent: AgentId | None = None


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
    agent: AgentId | None = None


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
    agent: AgentId | None = None


@dataclass(frozen=True)
class GateVerdict:
    """What a gate said about the run: the composed chain judging a tool call,
    or a surface's own hook at a stop.

    Content, not a signal: it is something that happened *in* the run, and the
    run goes on. Every verdict is recorded, a bare ``allow`` included — which
    gates ran on a call is the one thing no transcript carries.
    """

    point: GatePoint
    decision: GateDecision
    # the deciding hook; empty when nothing objected
    hook: str = ""
    reason: str = ""
    # advice fed back to the model, each piece with its author
    nudges: tuple[tuple[str, str], ...] = ()
    tool: str | None = None
    call_id: ToolCallId | None = None
    # which producer spoke: the chain itself, or a surface's transcript
    source: str | None = None
    ts: Timestamp | None = None
    agent: AgentId | None = None


@dataclass(frozen=True)
class PersonAsked:
    """A person is being waited on — for an answer, or for leave to run a tool.

    The one state a controller cannot infer from the stream: an unanswered
    call looks the same whether a tool is slow or a person is away. It has no
    closing twin: the wait is open while the call it names has no
    ``ToolResultReceived`` — the reading a response in flight already has —
    and that result, with any verdict beside it, says how it closed.
    """

    kind: AskKind
    call_id: ToolCallId | None = None
    tool: str | None = None
    # what a controller can show: the question, or the gate's reason
    detail: str | None = None
    # which producer spoke: a surface's reader, the chain, a surface's hook
    source: str | None = None
    ts: Timestamp | None = None
    agent: AgentId | None = None


Event = (
    RunStarted
    | RunEnded
    | PromptReceived
    | PersonAsked
    | ResponseStarted
    | ItemDelta
    | ItemEmitted
    | ToolResultReceived
    | ResponseEnded
    | GateVerdict
    | Signal
)
