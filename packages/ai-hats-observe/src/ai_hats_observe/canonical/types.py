"""Domain vocabulary: the scalars and the items a response is made of.

Everything a field can hold is named here, so no call site has to guess what a
bare ``str`` means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, NewType

# --- scalars ---------------------------------------------------------------

# ISO-8601 instant at which a surface says something happened.
Timestamp = NewType("Timestamp", str)

# Absolute wall-clock deadline, for waits that outlive this process.
EpochSeconds = NewType("EpochSeconds", int)

# Identity of one inference call. Stable across every fragment the surface emits for
# that call, which is what lets a consumer count a call — and its cost — exactly once.
ResponseId = NewType("ResponseId", str)

# Joins a tool invocation to its outcome.
ToolCallId = NewType("ToolCallId", str)

# Which model produced a response, so a switch mid-run is attributable.
ModelName = NewType("ModelName", str)


# --- items -----------------------------------------------------------------


class ItemKind(StrEnum):
    """The dimension a projection selects on: a consumer that scores an answer
    wants different items than one comparing how the answer was reached."""

    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class TextItem:
    """Prose addressed to the reader — the part of a run that is its answer."""

    kind: ClassVar[ItemKind] = ItemKind.TEXT
    text: str


@dataclass(frozen=True)
class ThinkingItem:
    """Reasoning the model did on the way to an answer. Kept verbatim so a
    comparison can ask *why* a run reached its result, and filtered out for
    consumers that may only judge the result itself."""

    kind: ClassVar[ItemKind] = ItemKind.THINKING
    text: str
    redacted: bool = False


@dataclass(frozen=True)
class ToolCallItem:
    """An action the model asked the harness to take."""

    kind: ClassVar[ItemKind] = ItemKind.TOOL_CALL
    # call_id is what a result later joins back to
    call_id: ToolCallId
    name: str
    input: dict[str, Any] = field(default_factory=dict)


Item = TextItem | ThinkingItem | ToolCallItem


# --- how a response ended --------------------------------------------------


class Completion(StrEnum):
    """Why a response stopped producing items.

    Only terminal outcomes appear here. A response still being produced has no
    completion at all — it is one for which no ``ResponseEnded`` has arrived —
    so "still running" can never be mistaken for a state the surface reported.
    """

    # The model finished on its own terms.
    COMPLETE = "complete"

    # The connection died mid-answer; what was received is partial.
    TRUNCATED_TRANSPORT = "truncated_transport"

    # An output ceiling cut the answer short; re-running with more room may complete it.
    TRUNCATED_BUDGET = "truncated_budget"

    # The model declined to answer.
    REFUSED = "refused"

    # Stopped from outside — an interrupt. Neither a failure nor a dropped
    # connection, so it must not read as one.
    CANCELLED = "cancelled"

    # The stream ended without the surface saying why. Distinct from every value above:
    # those are reported outcomes, this one is our admission that we did not observe
    # one.
    UNKNOWN = "unknown"


# --- gates -----------------------------------------------------------------


class GatePoint(StrEnum):
    """Where a gate sits in the run — named after the moment, never after a
    surface's own hook vocabulary, so every surface's hooks map onto three."""

    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    AT_STOP = "at_stop"


class GateDecision(StrEnum):
    """What a gate concluded. ``ask`` hands the call to a person; the run waits."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class Usage:
    """Billable cost of one inference call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )
