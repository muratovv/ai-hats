# ADR-0008: Pipeline Timeout & Cancellation Propagation

## Status
Accepted

## Context
Long-running or hung pipeline steps (LLM calls, sub-agent spawns, network
I/O) had no uniform timeout or cancellation mechanism. A single hung step
blocked the whole pipeline indefinitely: no per-step deadline, no
cooperative cancellation path, and no way to distinguish "timed out" from a
generic failure.

Steps are **synchronous** (`run(**inputs) -> dict`, projection-validated per
ADR-0001). Python cannot forcibly interrupt a synchronous call blocked in
arbitrary code (no preemptive kill without process/thread teardown). So a
robust design combines a hard wall-clock bound with a cooperative signal.

A partial timeout already existed at a **different layer**: the harness
(HATS-378) bounds the *sub-agent subprocess* at
`SUBAGENT_SUBPROCESS_TIMEOUT_S = 600` with a retry policy. That is a
subprocess-scoped bound inside `SubAgentRunner`, not a generic per-step
pipeline mechanism. This ADR adds the latter without duplicating the former.

## Decision
Retrofit timeout + cooperative cancellation onto the **existing** StepIO
projection model (ADR-0001). A new Step contract (`run(ctx) -> StepOutcome`,
`PipelineContext`, `StepStatus` enum, a separate `PipelineRunner`) was
prototyped and **rejected**: it was unmotivated by this requirement and
would have regressed shipped contracts (build-time requires/produces
validation, projection isolation, trace hooks HATS-274, harness_policy
HATS-378, the None-filter funnel of ADR-0005). Timeout/cancellation are
orthogonal to projection/validation, so they live in the existing runner.

### Per-step timeout, thread-bounded
`Step` gains an optional, additive `timeout: float | None` (default `None` =
current behaviour, no bound). When set, the sequential runner (`_run_steps`)
runs the step in a single-worker `ThreadPoolExecutor` and waits with
`future.result(timeout=...)`. It calls `shutdown(wait=False)` so the runner
never blocks on a hung step.

### Cooperative cancellation via `CancelToken`
A thread-safe **`CancelToken`** (`threading.Event` + reason, idempotent
first-reason-wins) is the cancellation signal. On a step timeout the runner
flips it (`CancelReason.TIMEOUT`); remaining steps are skipped at the next
boundary. `run(pipeline, initial, *, cancel_token=None)` lets an external
caller supply / flip a token from another thread to cancel cooperatively
(`CancelReason.EXTERNAL`). The token is **runner-level execution metadata**,
deliberately NOT part of the data funnel — ADR-0003 / ADR-0005 value
contracts are unchanged.

### Structured failure — no `StepStatus` enum
A timeout or external cancel raises a typed `PipelineCancelled(RuntimeError)`
carrying `reason: CancelReason` + the partial `state` (including any
`on_cancel` deltas), so callers can surface partial work. It is distinct from
`StepError` / a re-raised step exception, so a deadline/cancel is never
mistaken for a logic failure. The existing `failure_policy` `halt`/`continue`
path is unchanged. There is intentionally **no** `StepStatus` enum and no
`StepOutcome` wrapper — steps still return a plain `dict`; the runner
distinguishes a timeout internally.

### Cleanup hook
`Step` gains an optional `on_cancel(**inputs) -> dict | None` (default
no-op). On timeout the runner invokes it and merges the returned partial-
result delta via the None-filter rule (ADR-0005 П3); keys outside the step's
declared `produces` are dropped, and a raising `on_cancel` is logged and
swallowed (cleanup must never crash the cancellation path). This is the
channel for releasing resources — e.g. a process-group kill — and surfacing
partial work.

### Two timeout layers, reconciled (not duplicated)
- **Harness layer (HATS-378)** — authoritative bound for the sub-agent
  subprocess (`600 s` + retry), inside `SubAgentRunner`.
- **Pipeline-runner layer (this ADR)** — the generic, outer per-step net.

They are different scopes. A step that owns a killable subprocess closes the
gap by spawning with `start_new_session=True` and doing `os.killpg` in
`on_cancel`.

### Scope note — cancellation is boundary-detected
The runner observes cancellation at **step boundaries**; it does not
interrupt a step mid-run (synchronous code cannot be force-interrupted). So
an external cancel of a long-running step (e.g. the `provider` step blocked
inside `SubAgentRunner`) is not acted on until that step returns. Making
`SubAgentRunner` cancel-aware — threading the token in and `killpg`-ing the
in-flight sub-agent process tree — is deferred to **HATS-585**. HATS-584
ships the generic mechanism plus a unit-tested `on_cancel` process-group-kill
pattern at the subprocess boundary.

## Consequences
- A hung/slow step is bounded by a configurable per-step `timeout`.
- Timeout and cancellation are observable as a distinct, structured
  `PipelineCancelled`, never a generic crash; partial work survives via
  `on_cancel`.
- **Orphan threads.** A timed-out step keeps running in its worker thread; it
  cannot be force-killed. `shutdown(wait=False)` means the runner never blocks
  on it. Well-behaved steps release their resource in `on_cancel` (e.g.
  `killpg`). Accepted limitation of bounding synchronous code.
- **`on_cancel` / `run` race.** A step's `run` may still be in-flight in the
  orphan thread when the runner calls `on_cancel`. `on_cancel` MUST be safe to
  call concurrently with an in-flight `run` — snapshot / release only (e.g.
  kill by a stored pgid; never touch a live `Popen` from the cleanup thread).
- External cancel of a long-running step is not honoured mid-run until the
  cancel-aware runner work (HATS-585) lands.

## References
- ADR-0001 (pipelines as typed dataflow — `Step` = `run(**inputs) -> dict` +
  `StepIO` projection; pipeline = step recursion)
- ADR-0005 (composition & pipeline value contract — None-filter funnel, П3)
- HATS-274 (pipeline trace hooks)
- HATS-378 (harness reliability policy — sub-agent subprocess timeout/retry)
- HATS-585 (follow-up — cancel-aware `SubAgentRunner`)
