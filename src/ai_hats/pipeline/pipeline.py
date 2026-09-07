"""Pipeline + build/run per ADR-0001 §2-§3.

A ``Pipeline`` is itself a ``Step`` (recursive composition). ``build`` is a
two-phase entry point: it constructs the pipeline structure and validates
self-consistency (every step's ``requires`` must be either in the
pipeline's external requires or produced by an earlier step). ``run``
executes steps sequentially, threading state via projection — each step
sees only the keys it declared.

Optional ``on_step`` callback: when supplied to ``run``, the inner loop emits
one ``TraceEvent`` after every step (success or halt-failure) for
observability. ``on_step=None`` is the zero-overhead default; the trace branch
never executes.
"""

from __future__ import annotations

import inspect
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
    """Raised when a pipeline run is cancelled before completing.

    Cause is either a per-step ``timeout`` (``CancelReason.TIMEOUT``) or an
    external caller flipping the supplied ``cancel_token``
    (``CancelReason.EXTERNAL``). Carries the partial ``state`` accumulated up
    to the cancellation point — including any ``on_cancel`` deltas — so the
    caller can surface partial work. Distinct from ``StepError`` / a re-raised
    step exception so a deadline/cancel is never mistaken for a logic failure.
    """

    def __init__(self, message: str, *, reason: CancelReason, state: dict[str, Any]) -> None:
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
    name: str = "pipeline"
    failure_policy: FailurePolicy = "halt"

    def __init__(
        self,
        steps: tuple[Step, ...] | list[Step],
        name: str = "pipeline",
        failure_policy: FailurePolicy = "halt",
        pipeline_name: str | None = None,
    ) -> None:
        object.__setattr__(self, "steps", tuple(steps))
        eff_name = pipeline_name if pipeline_name is not None else name
        object.__setattr__(self, "name", eff_name)
        object.__setattr__(self, "failure_policy", failure_policy)

    @property
    def pipeline_name(self) -> str:
        """Backward-compatible alias for name."""
        return self.name

    @property
    def io(self) -> StepIO:
        produced: set[str] = set()
        external_req: set[str] = set()
        external_opt: set[str] = set()
        for s in self.steps:
            external_req |= s.io.requires - produced
            external_opt |= s.io.optional - produced - external_req
            produced |= s.io.produces
        return StepIO(
            name=self.name,
            requires=frozenset(external_req),
            optional=frozenset(external_opt),
            produces=frozenset(produced),
        )

    def run(
        self,
        initial: Mapping[str, Any] | None = None,
        *,
        on_step: TraceHook | None = None,
        trace_values: bool = False,
        cancel_token: CancelToken | None = None,
        **inputs: Any,
    ) -> dict[str, Any]:
        """Execute pipeline against initial state or inputs, threading projections."""
        state = dict(initial or {})
        state.update(inputs)
        return _execute_pipeline(
            self.steps,
            state,
            on_step=on_step,
            trace_values=trace_values,
            cancel_token=cancel_token,
            failure_policy=self.failure_policy,
        )


def _check_overwrites(steps: tuple[Step, ...]) -> None:
    """Refuse a producer whose output is overwritten before any step reads it.

    Two steps producing one key is legal — the interleaved shape reads each
    value before the next overwrites it. Only an *unread* overwrite is a defect,
    and telling them apart needs step order, which this walk has (HATS-1249).

    "Read" is tracked per producer STEP, not per key: consuming any one of a
    step's outputs clears all of them, because per-key strictness would reject
    the legitimate interleaved shape. See ADR-0001 §Update HATS-1249.
    """
    owner: dict[str, int] = {}
    read: set[int] = set()
    for i, s in enumerate(steps):
        for k in s.io.requires | s.io.optional:
            if k in owner:
                read.add(owner[k])
        lost: dict[int, list[str]] = {}
        for k in sorted(s.io.produces):
            prev = owner.get(k)
            if prev is not None and prev not in read:
                lost.setdefault(prev, []).append(k)
        if lost:
            detail = "; ".join(
                f"{keys} produced by {steps[p].io.name!r}" for p, keys in sorted(lost.items())
            )
            raise BuildError(
                f"{s.io.name}: overwrites {detail} — nothing in between reads "
                f"those keys, so the values would be lost silently. Put a "
                f"consumer between the two producers, or drop one of them."
            )
        for k in s.io.produces:
            owner[k] = i


def _required_run_params(step: Step) -> frozenset[str]:
    """The params ``step.run`` cannot be called without — bound, so no ``self``."""
    return frozenset(
        name
        for name, p in inspect.signature(step.run).parameters.items()
        if p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    )


def _check_run_signature(steps: tuple[Step, ...]) -> None:
    """Refuse a step whose ``run`` demands a param its ``io`` never declares.

    ``_run_steps`` projects kwargs strictly from the declaration, so an
    undeclared param is never passed and every call raises ``TypeError`` — under
    ``failure_policy="continue"`` the step is then skipped for its whole life
    while the pipeline stays green (HATS-1892).

    Strict against ``requires`` alone: ``optional`` means the key may be absent,
    and a required param riding an absent key is the same TypeError.
    """
    for s in steps:
        undeclared = _required_run_params(s) - s.io.requires
        if not undeclared:
            continue
        as_optional = sorted(undeclared & s.io.optional)
        hint = (
            f" {as_optional} are declared `optional`, which permits the key to be "
            "absent — a required param cannot ride one."
            if as_optional
            else ""
        )
        raise BuildError(
            f"{s.io.name}: run() cannot be called without {sorted(undeclared)}, "
            f"which io.requires does not declare ({sorted(s.io.requires)}) — the "
            f"runner projects kwargs from the declaration, so the step would raise "
            f"TypeError on every run.{hint}"
        )


def build(*steps: Step, name: str = "pipeline") -> Pipeline:
    """Construct a Pipeline. Validation against actual inputs is in ``run``."""
    _check_overwrites(tuple(steps))
    _check_run_signature(tuple(steps))
    return Pipeline(steps=tuple(steps), name=name)


def run(
    pipeline: Pipeline,
    initial: Mapping[str, Any] | None = None,
    *,
    on_step: TraceHook | None = None,
    trace_values: bool = False,
    cancel_token: CancelToken | None = None,
    **inputs: Any,
) -> dict[str, Any]:
    """Execute pipeline against ``initial`` state or kwargs, threading projections.

    ``on_step``: optional observability callback invoked after every step
    (success or halt-failure) with a ``TraceEvent``. Default ``None``
    keeps the loop allocation-free.
    ``trace_values``: when True, events carry truncated repr's of the
    actual key values (not just their names). Off by default — keys
    only — to avoid leaking prompt contents to disk.
    ``cancel_token``: optional caller-supplied cancellation signal. An external
    thread may flip it to cancel the run at the next step boundary; the runner
    also creates one implicitly when a step times out. Either way a cancelled run
    raises ``PipelineCancelled``.
    """
    return pipeline.run(
        initial,
        on_step=on_step,
        trace_values=trace_values,
        cancel_token=cancel_token,
        **inputs,
    )


def _execute_pipeline(
    steps: tuple[Step, ...],
    initial_state: Mapping[str, Any],
    *,
    on_step: TraceHook | None = None,
    trace_values: bool = False,
    cancel_token: CancelToken | None = None,
    failure_policy: FailurePolicy = "halt",
) -> dict[str, Any]:
    """Unified execution kernel: pre-flight check, sequential step loop, and cancel handling."""
    del failure_policy  # reserved for future composite policy extensions
    # Repeated here for a Pipeline constructed directly, bypassing ``build``.
    _check_overwrites(steps)
    _check_run_signature(steps)
    available = set(initial_state.keys())
    produced: set[str] = set()
    for s in steps:
        missing = s.io.requires - produced - available
        if missing:
            raise BuildError(
                f"{s.io.name}: undeclared requires {sorted(missing)} "
                f"(not in initial keys and not produced by prior steps)"
            )
        produced |= s.io.produces

    return _run_steps(
        steps,
        dict(initial_state),
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
    on_step: TraceHook | None = None,
    trace_values: bool = False,
    cancel_token: CancelToken | None = None,
) -> dict[str, Any]:
    """Sequential execution with projection→step.run→delta-merge."""
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
        # and the trace hook apply — never a bare KeyError that escapes both.
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
                    on_step,
                    s.io.name,
                    kwargs,
                    {},
                    duration_ms,
                    error=to,
                    include_values=trace_values,
                )
            _run_on_cancel(s, kwargs, state)
            break
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            if on_step is not None:
                _emit(
                    on_step,
                    s.io.name,
                    kwargs,
                    {},
                    duration_ms,
                    error=e,
                    include_values=trace_values,
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
                on_step,
                s.io.name,
                kwargs,
                delta,
                duration_ms,
                error=None,
                include_values=trace_values,
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


def _run_on_cancel(step: Step, kwargs: dict[str, Any], state: dict[str, Any]) -> None:
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
            "on_cancel for step %r raised; ignoring",
            step.io.name,
            exc_info=True,
        )
        return
    if not delta:
        return
    allowed = {k: v for k, v in delta.items() if k in step.io.produces}
    _merge_none_filtered(state, allowed)


def _merge_none_filtered(state: dict[str, Any], delta: Mapping[str, Any]) -> None:
    """Merge a step delta into state, dropping keys whose value is ``None``.

    Pipeline funnel value contract per ADR-0005 §3. A ``None`` value is
    indistinguishable from an absent key in the funnel, so it is filtered at the
    merge boundary — a consumer cannot then distinguish "the step did not emit the
    key" from "the step emitted the key with value None". This prevents the
    empty-Optional-as-absent trap.

    ``""`` (and other falsy values like ``0``, ``False``, ``[]``) are
    intentionally NOT filtered — they are valid non-absent values whose
    semantics differ from "key absent". Steps that need "absent" must emit
    ``None`` (or omit the key entirely).
    """
    state.update({k: v for k, v in delta.items() if v is not None})
