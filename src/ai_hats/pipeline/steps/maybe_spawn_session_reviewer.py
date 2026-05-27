"""``maybe_spawn_session_reviewer`` step — auto-retro decision + spawn.

Single source of truth for the auto-retro spawn block, shared by
`finalize-hitl` (HITL / WrapRunner) and `finalize-subagent`
(SubAgent / SubAgentRunner) sub-pipelines (HATS-530).

Pre-HATS-530 this logic was inlined inside ``RunSessionEnd`` and
therefore fired ONLY in the HITL pipeline — SubAgent's
``finalize-subagent.yaml`` retained the pre-HATS-535 contract of
"no auto-retro for sub-agents". HATS-530 closes that asymmetry by
extracting the block into its own step which both pipelines now
include.

Three sub-phases, each wrapped in ``try/except (Exception,
KeyboardInterrupt)`` per the HATS-086 invariant (a second Ctrl+C
during cleanup must not propagate):

1. **Retro decision** — pure ``make_decision(project_dir, session_id)``
   + ``write_retro_log`` so the decision survives even if the spawn
   crashes.
2. **Session-reviewer spawn** — when ``retro.action == "run"`` AND not
   recursion-guarded by ``HATS_SKIP_RETRO=1``, fire
   ``_spawn_session_reviewer_background``.
3. **Return delta** — emit ``retro_decision`` so a downstream step
   (e.g. ``run_session_end``'s retro banner in HITL) can render
   user-visible context without re-computing.

``failure_policy = "continue"`` — finalization is best-effort.

The retro banner UI is NOT printed here on purpose — it's a
HITL-only side effect of ``RunSessionEnd`` (SubAgent has no TTY of
its own for user-visible banners). The decision is exposed via the
funnel so the banner step can read it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class MaybeSpawnSessionReviewer(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="maybe_spawn_session_reviewer",
            requires=frozenset({"session_id", "project_dir"}),
            produces=frozenset({"retro_decision"}),
        )

    def run(
        self,
        *,
        session_id: str,
        project_dir: Path,
        **_: Any,
    ) -> dict[str, Any]:
        from ...retro.auto_retro import (
            _spawn_session_reviewer_background,
            make_decision,
            write_retro_log,
        )

        retro_decision: dict | None = None
        try:
            retro_decision = make_decision(project_dir, session_id)
            write_retro_log(
                project_dir, session_id,
                "runtime", "decision",
                f"{retro_decision['action']}: {retro_decision['reason']}",
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("retro decision/log failed", exc_info=True)

        if (
            retro_decision is not None
            and retro_decision.get("action") == "run"
            and os.environ.get("HATS_SKIP_RETRO") != "1"
        ):
            try:
                _spawn_session_reviewer_background(project_dir, session_id)
            except (Exception, KeyboardInterrupt):
                logger.warning(
                    "session-reviewer spawn failed", exc_info=True,
                )

        if retro_decision is not None:
            return {"retro_decision": retro_decision}
        return {}
