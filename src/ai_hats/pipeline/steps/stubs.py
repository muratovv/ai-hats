"""No-op pre/post steps that fix the interface shape for Phase 2/3 authors.

Two flavours:

- ``PreStub`` / ``PostStub`` — silent no-ops; minimal reference contract.
- ``PreLogStub`` / ``PostLogStub`` — print incoming kwargs to stderr so a
  developer can *see* the step fire when running a pipeline live. Used
  inside ``execute_pipeline`` so ``ai-hats execute`` shows the pre/post
  nodes in action without any business logic yet.

All four use ``failure_policy="continue"``: a stub should never abort the
surrounding flow.
"""

from __future__ import annotations

import sys
from typing import Any

from ..step import Step, StepIO


# Keys carried by ``execute_pipeline`` initial state — also LaunchProvider's
# requires + optional. Listing them here is the price the log-stubs pay
# for staying within the ADR projection contract: a step gets only the
# keys it declares.
_EXECUTE_INPUT_KEYS = frozenset({
    "interactive", "role", "provider", "prompt_text",
    "model", "isolation", "ticket", "tags", "extra_args",
})

_EXECUTE_OUTPUT_KEYS = frozenset({"session", "exit_code"})


class PreStub(Step):
    """Placeholder pre-step. Replaced in Phase 2 (HATS-266) by
    ``ResolvePrompt`` + ``ComposeRole`` + ``BuildMetaPrompt``.
    """

    failure_policy = "continue"

    @property
    def io(self) -> StepIO:
        return StepIO(name="pre_stub")

    def run(self, **inputs: Any) -> dict[str, Any]:
        return {}


class PostStub(Step):
    """Placeholder post-step. Replaced in Phase 3 (HATS-267) by real
    post-step chains (CollectMetrics / ExtractMarker / ...).

    Parametrized by the upstream key the post-side conceptually consumes:
    pipelines that produce ``session`` (execute / agent / bare / reflect-all)
    pass ``frozenset({"session"})``; pipelines that produce ``review_path``
    (reflect-session) pass ``frozenset({"review_path"})``. This proves at
    build-time that the upstream really emitted what downstream expects.
    """

    failure_policy = "continue"

    def __init__(self, requires: frozenset[str] = frozenset()) -> None:
        self._requires = requires

    @property
    def io(self) -> StepIO:
        return StepIO(name="post_stub", requires=self._requires)

    def run(self, **inputs: Any) -> dict[str, Any]:
        del inputs  # placeholder reads but does not act
        return {}


class PreLogStub(Step):
    """Pre-step that prints each known execute_pipeline input to stderr.

    Used by ``execute_pipeline`` so a real ``ai-hats execute`` invocation
    visibly fires a pre-node before launch. Phase 2 will replace this
    with ``ResolvePrompt`` / ``ComposeRole`` / ``BuildMetaPrompt``.
    """

    failure_policy = "continue"

    @property
    def io(self) -> StepIO:
        return StepIO(name="pre_log_stub", optional=_EXECUTE_INPUT_KEYS)

    def run(self, **inputs: Any) -> dict[str, Any]:
        print("[pipeline] pre_log_stub  fires", file=sys.stderr)
        for k in sorted(inputs):
            print(f"  in.{k} = {inputs[k]!r}", file=sys.stderr)
        return {}


class PostLogStub(Step):
    """Post-step that prints LaunchProvider outputs to stderr.

    Phase 3 will replace this with the real Collect/Finalize/Hooks/Retro
    chain. For now it just confirms the post-node is reached and shows
    what the launch step produced.
    """

    failure_policy = "continue"

    @property
    def io(self) -> StepIO:
        return StepIO(name="post_log_stub", optional=_EXECUTE_OUTPUT_KEYS)

    def run(self, **inputs: Any) -> dict[str, Any]:
        print("[pipeline] post_log_stub fires", file=sys.stderr)
        for k in sorted(inputs):
            print(f"  in.{k} = {inputs[k]!r}", file=sys.stderr)
        return {}
