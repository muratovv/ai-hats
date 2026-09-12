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

    # Capacity returns by itself; ``retry_after`` says when.
    WAIT = "wait"

    # Transient upstream failure, worth another attempt now.
    RETRY = "retry"

    # Retrying cannot help — the request itself is wrong, or the cause is unknown.
    ABORT = "abort"


class WorthRecording(StrEnum):
    """The run continues; a reader may still need to know this happened."""

    MODEL_SWITCHED = "model_switched"
    CONTEXT_COMPACTED = "context_compacted"

    # The surface reported something a reader should know that is not a failure
    # and not drift — a degraded feature, an optional connection lost. Distinct
    # from UNSUPPORTED_RECORD, which must stay rare enough to mean schema drift.
    SURFACE_WARNING = "surface_warning"

    # Capacity is running low but nothing has been refused yet. Only a live
    # stream reports this, and only before the wall is hit — it is the one
    # signal that arrives in time to change what a caller does.
    APPROACHING_LIMIT = "approaching_limit"

    # A record shape we do not model. Reported rather than dropped so schema drift is
    # visible the first time it appears, instead of silently changing what our numbers
    # mean.
    UNSUPPORTED_RECORD = "unsupported_record"


@dataclass(frozen=True)
class _Signal:
    ts: Timestamp | None = None
    detail: str | None = None
    # the surface's own code, kept verbatim for forensics
    raw_code: str | None = None
    # which reader spoke: the two Claude sources see different things — only the
    # live stream carries quota pre-warnings, only the transcript carries status
    source: str | None = None


@dataclass(frozen=True)
class PersonActionRequired(_Signal):
    """Blocks the run until someone intervenes."""

    reason: PersonMustAct = PersonMustAct.REAUTHENTICATE


@dataclass(frozen=True)
class HarnessActionRequired(_Signal):
    """Blocks the run; the harness chooses the response."""

    reason: HarnessMustAct = HarnessMustAct.ABORT
    # when the blocking condition lifts, where the surface says so; whether to
    # wait for it is the caller's decision, not this record's
    retry_after: EpochSeconds | None = None


@dataclass(frozen=True)
class Notice(_Signal):
    """Does not block the run."""

    reason: WorthRecording = WorthRecording.UNSUPPORTED_RECORD
    # on MODEL_SWITCHED: which model took over
    model: ModelName | None = None


Signal = PersonActionRequired | HarnessActionRequired | Notice

# The two that end a run — what a consumer tests against to ask "did this survive",
# instead of reading a severity.
Blocking = PersonActionRequired | HarnessActionRequired
