"""Harness reliability policy for pipeline steps (HATS-378).

A ``HarnessPolicy`` is an opt-in, additive marker attached to a pipeline
step via its YAML config. The harness reads it after a sub-agent run to
decide whether to validate output, retry on timeout, or escalate to a
``harness-incident`` meta-PROP.

The role manifest stays free of harness knowledge — policy lives at the
step layer because it describes *how the harness runs the step*, not
*what the agent does*. A user who overrides a role does not need to
know about reliability; a user authoring a pipeline does.

YAML form (additive — current pipelines keep working when ``harness:``
is absent)::

    steps:
      - id: run_session_review
        params: { max_retries: 1 }
        harness:
          reporting: true
          on_zero_output: harness_incident
          on_timeout:
            retry: 1
            budget_multiplier: 2
            then: harness_incident
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


OnZeroOutput = Literal["harness_incident", "ignore"]
ThenAction = Literal["harness_incident"]


class HarnessPolicyError(ValueError):
    """Malformed ``harness:`` block in a pipeline step."""


@dataclass(frozen=True)
class TimeoutPolicy:
    """Retry policy applied when a sub-agent subprocess times out.

    ``retry`` — additional attempts after the first timeout. v0.6 ships
    with ``retry=1`` (one extra attempt at increased budget). Must be ≥0.
    ``budget_multiplier`` — wall-clock budget for each retry is the
    original budget × this factor. Must be ≥ 1.0.
    ``then`` — terminal action when retries are exhausted; currently
    only ``harness_incident`` is supported.
    """

    retry: int = 1
    budget_multiplier: float = 2.0
    then: ThenAction = "harness_incident"


@dataclass(frozen=True)
class HarnessPolicy:
    """Per-step harness reliability configuration.

    ``reporting`` — when True, the universal zero-output guard fires for
    runs that exit cleanly (``exit_code=0``, no timeout) yet emitted
    zero output tokens AND zero tool calls. Such runs are silent
    failures that previously were accepted.
    ``on_zero_output`` — terminal action when the zero-output guard
    trips. ``harness_incident`` files a meta-PROP under
    ``target=harness-incident``; ``ignore`` disables the guard
    explicitly even though ``reporting=True``.
    ``on_timeout`` — when present, applies retry-then-incident on
    subprocess timeouts. ``None`` means: keep existing behaviour (raise
    immediately, no harness-incident escalation).
    """

    reporting: bool = False
    on_zero_output: OnZeroOutput | None = None
    on_timeout: TimeoutPolicy | None = None


_HARNESS_KEYS = frozenset({"reporting", "on_zero_output", "on_timeout"})
_TIMEOUT_KEYS = frozenset({"retry", "budget_multiplier", "then"})


def parse_harness_policy(raw: Any) -> HarnessPolicy:
    """Parse a YAML ``harness:`` block into a ``HarnessPolicy``.

    Raises ``HarnessPolicyError`` on wrong types, unknown keys, or
    inconsistent values (negative retry, multiplier < 1.0, unknown
    ``then`` action). Empty mapping returns the default policy
    (reporting=False) and is treated as a no-op.
    """
    if not isinstance(raw, dict):
        raise HarnessPolicyError(
            f"harness must be a mapping, got {type(raw).__name__}"
        )

    unknown = set(raw.keys()) - _HARNESS_KEYS
    if unknown:
        raise HarnessPolicyError(
            f"unknown keys {sorted(unknown)} "
            f"(allowed: {sorted(_HARNESS_KEYS)})"
        )

    reporting_raw = raw.get("reporting", False)
    if not isinstance(reporting_raw, bool):
        raise HarnessPolicyError(
            f"reporting must be bool, got {type(reporting_raw).__name__}"
        )

    on_zero_output_raw = raw.get("on_zero_output")
    if on_zero_output_raw is not None and on_zero_output_raw not in (
        "harness_incident",
        "ignore",
    ):
        raise HarnessPolicyError(
            f"on_zero_output must be 'harness_incident' or 'ignore', "
            f"got {on_zero_output_raw!r}"
        )

    on_timeout = _parse_timeout(raw.get("on_timeout"))

    return HarnessPolicy(
        reporting=reporting_raw,
        on_zero_output=on_zero_output_raw,
        on_timeout=on_timeout,
    )


def _parse_timeout(raw: Any) -> TimeoutPolicy | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HarnessPolicyError(
            f"on_timeout must be a mapping, got {type(raw).__name__}"
        )
    unknown = set(raw.keys()) - _TIMEOUT_KEYS
    if unknown:
        raise HarnessPolicyError(
            f"on_timeout: unknown keys {sorted(unknown)} "
            f"(allowed: {sorted(_TIMEOUT_KEYS)})"
        )
    retry = raw.get("retry", 1)
    if not isinstance(retry, int) or isinstance(retry, bool) or retry < 0:
        raise HarnessPolicyError(
            f"on_timeout.retry must be a non-negative int, got {retry!r}"
        )
    multiplier = raw.get("budget_multiplier", 2.0)
    if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool):
        raise HarnessPolicyError(
            f"on_timeout.budget_multiplier must be a number, got "
            f"{type(multiplier).__name__}"
        )
    multiplier = float(multiplier)
    if multiplier < 1.0:
        raise HarnessPolicyError(
            f"on_timeout.budget_multiplier must be >= 1.0, got {multiplier}"
        )
    then = raw.get("then", "harness_incident")
    if then != "harness_incident":
        raise HarnessPolicyError(
            f"on_timeout.then must be 'harness_incident', got {then!r}"
        )
    return TimeoutPolicy(retry=retry, budget_multiplier=multiplier, then=then)
