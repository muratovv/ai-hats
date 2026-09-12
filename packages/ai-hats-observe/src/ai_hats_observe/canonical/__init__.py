"""DRAFT (HATS-1966) — the canonical, surface-agnostic session model.

Under review; not wired to anything yet.

``types``   the vocabulary a response is made of
``signals`` run health, split by who has to act
``events``  the stream vocabulary
``reader``  the per-surface readers that produce it
``views``   projections built over the stream
"""

from .events import (
    Event,
    ItemDelta,
    ItemEmitted,
    PromptReceived,
    ResponseEnded,
    ResponseStarted,
    ToolResultReceived,
)
from .reader import AsyncEventReader, EventReader
from .signals import (
    Blocking,
    HarnessActionRequired,
    HarnessMustAct,
    Notice,
    PersonActionRequired,
    PersonMustAct,
    Signal,
    WorthRecording,
)
from .types import (
    Completion,
    EpochSeconds,
    Item,
    ItemKind,
    ModelName,
    ResponseId,
    TextItem,
    ThinkingItem,
    Timestamp,
    ToolCallId,
    ToolCallItem,
    Usage,
)
from .views import ANSWER_ONLY, EVERYTHING, WITH_REASONING, Collected, collect, select

__all__ = [
    "ANSWER_ONLY",
    "Blocking",
    "Collected",
    "Completion",
    "EVERYTHING",
    "EpochSeconds",
    "Event",
    "HarnessActionRequired",
    "HarnessMustAct",
    "Item",
    "ItemDelta",
    "ItemEmitted",
    "ItemKind",
    "ModelName",
    "Notice",
    "PersonActionRequired",
    "PersonMustAct",
    "PromptReceived",
    "ResponseEnded",
    "ResponseId",
    "ResponseStarted",
    "Signal",
    "TextItem",
    "ThinkingItem",
    "Timestamp",
    "ToolCallId",
    "ToolCallItem",
    "ToolResultReceived",
    "Usage",
    "WITH_REASONING",
    "WorthRecording",
    "collect",
    "AsyncEventReader",
    "EventReader",
    "select",
]
