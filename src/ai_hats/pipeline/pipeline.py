"""Pipeline + build/run per ADR-0001 §2-§3.

A ``Pipeline`` is itself a ``Step`` (recursive composition). ``build`` is a
two-phase entry point: it constructs the pipeline structure and validates
self-consistency (every step's ``requires`` must be either in the
pipeline's external requires or produced by an earlier step). ``run``
executes steps sequentially, threading state via projection — each step
sees only the keys it declared.

Optional ``on_step`` callback (HATS-274): when supplied to ``run``, the
inner loop emits one ``TraceEvent`` after every step (success or
halt-failure) for observability. ``on_step=None`` is the zero-overhead
default; the trace branch never executes.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Mapping

from .cancel import CancelReason, CancelToken
from .step import FailurePolicy, Step, StepError, StepIO
from .trace import TraceHook, make_event

logger = logging.getLogger(__name__)


class BuildError(ValueError):
    """Build-time contract violation (e.g. undeclared requires)."""


class PipelineCancelled(RuntimeError):
    """Raised when a pipeline run is cancelled before completing (HATS-584).

    Cause is either a per-step ``timeout`` (``CancelReason.TIMEOUT``) or an
    external caller flipping the supplied ``cancel_token``
    (``CancelReason.EXTERNAL``). Carries the partial ``state`` accumulated up
    to the cancellation point — including any ``on_cancel`` deltas — so the
    caller can surface partial work. Distinct from ``StepError`` / a re-raised
    step exception so a deadline/cancel is never mistaken for a logic failure.
    """

    def __init__(
        self, message: str, *, reason: CancelReason, state: dict[str, Any]
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.state = state


class _StepTimeout(Exception):
    """Internal marker: a step blew its ``timeout``. Never escapes the module."""

    def __init__(self, step_name: str, timeout: float) -> None:
        super().__init__(f"step {step_name!r} exceeded timeout of {timeout}s")
        self.step_name = step_name
        self.timeout = timeout


@dataclass(frozen=True)
class Pipeline(Step):
    steps: tuple[Step, ...]
    pipeline_name: str = "pipeline"
    failure_policy: FailurePolicy = "halt"

    @property
    def io(self) -> StepIO:
        produced: set[str] = set()
        external_req: set[str] = set()
        external_opt: set[str] = set()
        for s in self.steps:
            external_req |= (s.io.requires - produced)
            external_opt |= (s.io.optional - produced - external_req)
            produced |= s.io.produces
        return StepIO(
            name=self.pipeline_name,
            requires=frozenset(external_req),
            optional=frozenset(external_opt),
            produces=frozenset(produced),
        )

    def run(self, **inputs: Any) -> dict[str, Any]:
        return _run_steps(
            self.steps, dict(inputs), parent_policy=self.failure_policy
        )


def build(*steps: Step, name: str = "pipeline") -> Pipeline:
    """Construct a Pipeline. Validation against actual inputs is in ``run``.

    Anything a step requires but no earlier step produces becomes part of
    the pipeline's external ``requires`` — the implicit set of keys that
    callers must supply via ``initial``. ``run`` is what checks those
    against the actual initial state.
    """
    return Pipeline(steps=tuple(steps), pipeline_name=name)


def run(
    pipeline: Pipeline,
    initial: Mapping[str, Any],
    *,
    on_step: TraceHook | None = None,
    trace_values: bool = False,
    cancel_token: CancelToken | None = None,
) -> dict[str, Any]:
    """Execute pipeline against ``initial`` state, threading projections.

    ``on_step``: optional observability callback invoked after every step
    (success or halt-failure) with a ``TraceEvent``. Default ``None``
    keeps the loop allocation-free.
    ``trace_values``: when True, events carry truncated repr's of the
    actual key values (not just their names). Off by default — keys
    only — to avoid leaking prompt contents to disk.
    ``cancel_token`` (HATS-584): optional caller-supplied cancellation
    signal. An external thread may flip it to cancel the run at the next
    step boundary; the runner also creates one implicitly when a step
    times out. Either way a cancelled run raises ``PipelineCancelled``.
    """
    available = set(initial.keys())
    produced: set[str] = set()
    for s in pipeline.steps:
        missing = s.io.requires - produced - available
        if missing:
            raise BuildError(
                f"{s.io.name}: undeclared requires {sorted(missing)} "
                f"(not in initial keys and not produced by prior steps)"
            )
        produced |= s.io.produces
    return _run_steps(
        pipeline.steps,
        dict(initial),
        parent_policy=pipeline.failure_policy,
        on_step=on_step,
        trace_values=trace_values,
        cancel_token=cancel_token,
    )


def _emit(
    on_step: TraceHook,
    step_name: str,
    requires_seen: dict[str, Any],
    produces: dict[str, Any],
    duration_ms: float,
    *,
    error: BaseException | None,
    include_values: bool,
) -> None:
    """Trace-emit wrapper that swallows hook failures.

    Trace is best-effort instrumentation, never business logic.
    """
    try:
        event = make_event(
            step_name,
            requires_seen,
            produces,
            duration_ms,
            error=error,
            include_values=include_values,
        )
        on_step(event)
    except Exception:  # noqa: BLE001 — trace must not abort pipeline
        logger.warning("trace hook raised; continuing", exc_info=True)


def _run_steps(
    steps: tuple[Step, ...],
    state: dict[str, Any],
    *,
    parent_policy: FailurePolicy,
    on_step: TraceHook | None = None,
    trace_values: bool = False,
    cancel_token: CancelToken | None = None,
) -> dict[str, Any]:
    """Sequential execution with projection→step.run→delta-merge.

    HATS-584: a step that declares a ``timeout`` is bounded in a worker
    thread. On the deadline the shared ``cancel_token`` is flipped
    (creating one if the caller supplied none), the step's ``on_cancel``
    cleanup runs, remaining steps are skipped, and ``PipelineCancelled`` is
    raised carrying the partial state. A caller-supplied ``cancel_token``
    that an external thread flips is observed at the next step boundary and
    cancels cooperatively the same way.
    """
    del parent_policy  # reserved for nested composites; not used in Phase 1
    token = cancel_token
    for s in steps:
        if token is not None and token.cancelled:
            # Cooperative propagation: external cancel observed at a step
            # boundary. The step never starts — nothing to clean up — so we
            # simply stop and let the post-loop guard raise.
            break
        # Project only keys actually present. ``requires`` is validated at build
        # time against *declared* upstream produces, but a producer may legally
        # omit a declared key at runtime (None-filtered merge; ComposeRole emits
        # {} for no role — ADR-0005 value contract). A non-raising projection
        # keeps ``kwargs`` defined for the ``except`` _emit calls; the presence
        # check below raises a typed StepError INSIDE the try so failure_policy
        # and the trace hook apply (HATS-739) — never a bare KeyError that
        # escapes both.
        kwargs = {k: state[k] for k in s.io.requires if k in state}
        kwargs.update({k: state[k] for k in s.io.optional if k in state})
        t0 = time.perf_counter()
        try:
            missing = sorted(s.io.requires - state.keys())
            if missing:
                raise StepError(
                    f"{s.io.name}: required context keys {missing} absent at "
                    f"runtime (declared by an upstream produces but not emitted "
                    f"— None-filtered or omitted per the ADR-0005 value contract)"
                )
            delta = _run_one(s, kwargs)
        except _StepTimeout as to:
            duration_ms = (time.perf_counter() - t0) * 1000
            if token is None:
                token = CancelToken()
            token.cancel(CancelReason.TIMEOUT)
            if on_step is not None:
                _emit(
                    on_step, s.io.name, kwargs, {}, duration_ms,
                    error=to, include_values=trace_values,
                )
            _run_on_cancel(s, kwargs, state)
            break
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            if on_step is not None:
                _emit(
                    on_step, s.io.name, kwargs, {}, duration_ms,
                    error=e, include_values=trace_values,
                )
            if s.failure_policy == "halt":
                raise
            state.setdefault("errors", {})[s.io.name] = e
            continue
        duration_ms = (time.perf_counter() - t0) * 1000
        unexpected = set(delta.keys()) - s.io.produces
        if unexpected:
            raise StepError(
                f"{s.io.name}: emitted unexpected keys {sorted(unexpected)} "
                f"(declared produces: {sorted(s.io.produces)})"
            )
        if on_step is not None:
            _emit(
                on_step, s.io.name, kwargs, delta, duration_ms,
                error=None, include_values=trace_values,
            )
        _merge_none_filtered(state, delta)

    if token is not None and token.cancelled:
        reason = token.reason or CancelReason.EXTERNAL
        raise PipelineCancelled(
            f"pipeline cancelled ({reason.value})",
            reason=reason,
            state=state,
        )
    return state


def _run_one(step: Step, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Run a step, bounding it in a worker thread iff it declares a timeout.

    The ``ThreadPoolExecutor`` is used WITHOUT its context manager on
    purpose: ``__exit__`` calls ``shutdown(wait=True)`` which would block on
    a hung step. ``shutdown(wait=False)`` lets the orphaned worker thread
    finish on its own — the step keeps running until it returns or its
    resource is released by ``on_cancel`` (e.g. a process-group kill). This
    orphan is an accepted limitation of bounding synchronous code (ADR-0008).
    """
    timeout = step.timeout
    if timeout is None:
        return step.run(**kwargs)
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(step.run, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        raise _StepTimeout(step.io.name, timeout) from None
    finally:
        pool.shutdown(wait=False)


def _run_on_cancel(
    step: Step, kwargs: dict[str, Any], state: dict[str, Any]
) -> None:
    """Invoke a step's ``on_cancel`` cleanup and merge its partial delta.

    Cleanup must never abort the cancellation path: a raising ``on_cancel``
    is logged and swallowed. Only keys the step declared in ``produces`` are
    merged (others dropped) so cleanup cannot smuggle undeclared keys into
    the funnel; the None-filter rule still applies.
    """
    try:
        delta = step.on_cancel(**kwargs)
    except Exception:  # noqa: BLE001 — cleanup must not crash cancellation
        logger.warning(
            "on_cancel for step %r raised; ignoring", step.io.name,
            exc_info=True,
        )
        return
    if not delta:
        return
    allowed = {k: v for k, v in delta.items() if k in step.io.produces}
    _merge_none_filtered(state, allowed)


def _merge_none_filtered(state: dict[str, Any], delta: Mapping[str, Any]) -> None:
    """Merge a step delta into state, dropping keys whose value is ``None``.

    HATS-452 (П3 in ADR-0005): pipeline funnel value contract. A ``None``
    value is indistinguishable from an absent key in the funnel, so it is
    filtered at the merge boundary — a consumer cannot then distinguish "the
    step did not emit the key" from "the step emitted the key with value
    None". This prevents the empty-Optional-as-absent trap that broke
    HATS-452 (``compose_role`` returned ``{"system_prompt": ""}`` for a
    missing role; downstream consumed ``""`` as a legitimate override).

    ``""`` (and other falsy values like ``0``, ``False``, ``[]``) are
    intentionally NOT filtered — they are valid non-absent values whose
    semantics differ from "key absent". Steps that need "absent" must emit
    ``None`` (or omit the key entirely).
    """
    state.update({k: v for k, v in delta.items() if v is not None})
