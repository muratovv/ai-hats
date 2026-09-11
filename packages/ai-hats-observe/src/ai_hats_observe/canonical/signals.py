"""Run-health signals, split by the domain that has to act on them.

A consumer's first question about a failure is never "how severe is it" but
"whose problem is it": does a person have to do something, does the harness have
to do something, or is this only worth recording? Those three answers are three
types, so the obligation is carried by the type itself and cannot drift out of
step with a separate severity field.

Naming here follows the obligation, never the surface's own error vocabulary, so
a second surface maps onto these instead of adding a parallel taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .types import EpochSeconds, ModelName, Timestamp


class PersonMustAct(StrEnum):
    """Nothing automated can clear these; the run is over until a human moves."""

    REAUTHENTICATE = "reauthenticate"
    PAY = "pay"
    RAISE_LIMIT = "raise_limit"


class HarnessMustAct(StrEnum):
    """The harness can decide what to do without a person."""

    WAIT = "wait"
    """Capacity will return by itself; ``retry_after`` says when."""

    RETRY = "retry"
    """Transient upstream failure, worth another attempt now."""

    ABORT = "abort"
    """Retrying cannot help — the request itself is wrong, or the cause is
    unknown. Stop and surface it."""


class WorthRecording(StrEnum):
    """The run continues; a reader may still need to know this happened."""

    MODEL_SWITCHED = "model_switched"
    CONTEXT_COMPACTED = "context_compacted"
    UNSUPPORTED_RECORD = "unsupported_record"
    """A record shape we do not model. Reported rather than dropped so schema
    drift is visible the first time it appears, instead of silently changing
    what our numbers mean."""


@dataclass(frozen=True)
class _Signal:
    ts: Timestamp | None = None
    detail: str | None = None
    raw_code: str | None = None
    """The surface's own code, kept verbatim for forensics."""

    source: str | None = None
    """Which reader produced this. The two Claude sources see different things —
    only the live stream carries quota pre-warnings, only the transcript carries
    HTTP status — so a consumer sometimes needs to know which one spoke."""


@dataclass(frozen=True)
class PersonActionRequired(_Signal):
    """Blocks the run until someone intervenes."""

    reason: PersonMustAct = PersonMustAct.REAUTHENTICATE


@dataclass(frozen=True)
class HarnessActionRequired(_Signal):
    """Blocks the run; the harness chooses the response."""

    reason: HarnessMustAct = HarnessMustAct.ABORT
    retry_after: EpochSeconds | None = None
    """When the blocking condition lifts, when the surface tells us. A fact
    about the failure — deciding whether to wait for it belongs to the caller."""


@dataclass(frozen=True)
class Notice(_Signal):
    """Does not block the run."""

    reason: WorthRecording = WorthRecording.UNSUPPORTED_RECORD
    model: ModelName | None = None
    """Set on ``MODEL_SWITCHED``: which model took over."""


Signal = PersonActionRequired | HarnessActionRequired | Notice

Blocking = PersonActionRequired | HarnessActionRequired
"""The two that end a run. A consumer asking only "did this run survive" tests
against this pair rather than reading a severity."""
