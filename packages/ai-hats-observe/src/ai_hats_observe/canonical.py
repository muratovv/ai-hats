"""DRAFT (HATS-1966) — the canonical, surface-agnostic session model.

Not wired to anything yet and not expected to run: this file exists so the
shape of the API can be reviewed before it is built out.

Two axes, because consumers ask two different questions:

* **Content** — what happened in the dialogue. ``Exchange`` -> ``Response`` ->
  items. The unit that matters is the ``Response``: ONE API call, identified by
  ``requestId``. A Claude JSONL *record* is a fragment of a response, not a
  response — 75.8% of responses span two or more records, which is why summing
  per-record ``usage`` inflates every token number we publish by 2.61x.
* **Health** — what happened to the run. ``SessionSignal``. Its ``kind`` names a
  decision a consumer has to make (does a human need to act? can this be
  retried?), never a provider's field name, so ``codex``/``agy``/``cline`` map
  onto it instead of growing a parallel taxonomy.

``transient`` and ``retry_after`` are recorded as facts about a failure. Acting
on them is execution and lives in a different card.

Open naming question for review: this module is ``canonical``; the dialogue unit
is ``Exchange`` because ``Turn`` is taken by the legacy shape it replaces. Names
go into ``docs/glossary.md`` once settled — deliberately not done yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, Iterable, Iterator

# ---------------------------------------------------------------------------
# Content axis
# ---------------------------------------------------------------------------


class ItemKind(StrEnum):
    TEXT = "text"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class TextItem:
    """What the model said. Every one is kept — today 45.3% of turns lose all
    but the last, discarding 3.6M characters across the corpus."""

    kind: ClassVar[ItemKind] = ItemKind.TEXT
    text: str


@dataclass(frozen=True)
class ThinkingItem:
    """Reasoning, retained verbatim. Replaces ``thinking_secs``, which reported
    ``len(thinking) // 200`` as if it were a measurement in seconds."""

    kind: ClassVar[ItemKind] = ItemKind.THINKING
    text: str
    redacted: bool = False


@dataclass(frozen=True)
class ToolCallItem:
    kind: ClassVar[ItemKind] = ItemKind.TOOL_CALL
    call_id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultItem:
    """``call_id`` links back to its ``ToolCallItem`` — the link today does not
    exist, and the 3458 failing results in the corpus reach no artifact."""

    kind: ClassVar[ItemKind] = ItemKind.TOOL_RESULT
    call_id: str
    ok: bool
    content: Any = None


Item = TextItem | ThinkingItem | ToolCallItem | ToolResultItem


class Completion(StrEnum):
    """Why a response stopped producing items."""

    COMPLETE = "complete"
    TRUNCATED_TRANSPORT = "truncated_transport"      # connection lost / machine slept
    TRUNCATED_MAX_TOKENS = "truncated_max_tokens"    # 1 occurrence in 168,179 records
    REFUSED = "refused"
    IN_FLIGHT = "in_flight"
    """No terminal fragment seen. The tail of a live session and the footprint of
    a run that died look identical; this is the value HATS-1967 tails on."""


@dataclass(frozen=True)
class Usage:
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


@dataclass(frozen=True)
class Response:
    """One API call's output, assembled from however many records carried it."""

    id: str
    """``requestId``, falling back to ``message.id`` then ``uuid``. This is the
    dedup key: repeat fragments carry a byte-identical ``usage`` (6166 observed,
    0 differing), so usage is taken once per id and never summed per record."""

    model: str | None = None
    usage: Usage = field(default_factory=Usage)
    completion: Completion = Completion.IN_FLIGHT
    items: tuple[Item, ...] = ()
    ts: str | None = None
    stop_reason: str | None = None

    def of(self, *kinds: ItemKind) -> Iterator[Item]:
        return (i for i in self.items if i.kind in kinds)

    @property
    def text(self) -> str:
        """All of what the model said, in order — not just the last block."""
        return "".join(i.text for i in self.items if i.kind is ItemKind.TEXT)


@dataclass(frozen=True)
class Exchange:
    """A prompt and the responses it provoked. Replaces the legacy ``Turn``."""

    ts: str | None = None
    prompt: str | None = None
    responses: tuple[Response, ...] = ()

    @property
    def usage(self) -> Usage:
        total = Usage()
        for r in self.responses:
            total = total + r.usage
        return total


# ---------------------------------------------------------------------------
# Health axis
# ---------------------------------------------------------------------------


class SignalKind(StrEnum):
    """Named after the consumer's decision, not after a provider's field."""

    AUTH = "auth"                          # a human must re-authenticate
    BILLING = "billing"                    # a human must pay
    QUOTA = "quota"                        # wait; carries retry_after
    SERVICE = "service"                    # transient upstream failure
    INVALID_REQUEST = "invalid_request"    # our bug; terminal
    REFUSAL = "refusal"                    # declined by policy
    MODEL_SWITCH = "model_switch"          # the model changed mid-run
    CONTEXT_COMPACTION = "context_compaction"
    UNSUPPORTED = "unsupported"            # we did not understand this record
    UNKNOWN = "unknown"                    # it failed; the platform did not say why


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class SessionSignal:
    kind: SignalKind
    severity: Severity
    ts: str | None = None
    detail: str | None = None
    transient: bool = False
    """Whether a retry could plausibly succeed. A fact, not a policy — nothing
    in this card acts on it."""

    retry_after: int | None = None
    """Epoch seconds, from ``quotaLimits.resetsAt`` / ``RateLimitInfo.resets_at``."""

    raw_code: str | None = None
    """The surface's own code, kept for forensics: ``"429"``, ``"server_error"``,
    or the unrecognized record type that produced an ``UNSUPPORTED``."""

    source: str | None = None
    """``"claude/jsonl"`` | ``"claude/sdk"`` — the two sources disagree by design
    and a consumer sometimes needs to know which one spoke."""

    @property
    def blocks_run(self) -> bool:
        return self.severity is Severity.ERROR


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class View:
    """A per-consumer projection: a judge does not need to know *why* an output
    was produced, an A/B comparison does."""

    include: frozenset[ItemKind]

    def apply(self, exchanges: Iterable[Exchange]) -> tuple[Exchange, ...]:
        out = []
        for ex in exchanges:
            responses = tuple(
                Response(
                    id=r.id,
                    model=r.model,
                    usage=r.usage,
                    completion=r.completion,
                    items=tuple(i for i in r.items if i.kind in self.include),
                    ts=r.ts,
                    stop_reason=r.stop_reason,
                )
                for r in ex.responses
            )
            out.append(Exchange(ts=ex.ts, prompt=ex.prompt, responses=responses))
        return tuple(out)


VIEW_FULL = View(include=frozenset(ItemKind))
VIEW_JUDGE = View(include=frozenset({ItemKind.TEXT, ItemKind.TOOL_CALL, ItemKind.TOOL_RESULT}))
VIEW_AB = VIEW_FULL


# ---------------------------------------------------------------------------
# Adapter contract — one per surface; Claude ships both, the rest come later
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalSession:
    """What every adapter returns."""

    exchanges: tuple[Exchange, ...] = ()
    signals: tuple[SessionSignal, ...] = ()

    @property
    def usage(self) -> Usage:
        total = Usage()
        for ex in self.exchanges:
            total = total + ex.usage
        return total

    @property
    def failed(self) -> SessionSignal | None:
        """The first error-severity signal, if the platform killed this run."""
        return next((s for s in self.signals if s.blocks_run), None)


def from_jsonl(records: Iterable[dict]) -> CanonicalSession:
    """Claude transcript adapter: records -> responses, grouped by ``requestId``."""
    raise NotImplementedError("S1")


def from_sdk(messages: Iterable[object]) -> CanonicalSession:
    """Claude Agent SDK adapter: the same shape from the message stream."""
    raise NotImplementedError("S4")
