"""``maybe_spawn_session_reviewer`` step — auto-retro decision + spawn.

Single source of truth for the auto-retro spawn block, shared by `finalize-hitl`
(WrapRunner) and `finalize-subagent` (SubAgentRunner) sub-pipelines (HATS-530,
which closed the prior HITL-only asymmetry).

Three sub-phases, each wrapped in ``try/except (Exception, KeyboardInterrupt)``
per the HATS-086 invariant (a second Ctrl+C during cleanup must not propagate):

1. **Retro decision** — ``make_decision`` + ``write_retro_log`` so the decision
   survives even if the spawn crashes.
2. **Spawn** — when ``retro.action == "run"`` and not ``HATS_SKIP_RETRO=1``, fire
   ``_spawn_session_reviewer_background``.
3. **Return delta** — emit ``retro_decision`` for a downstream banner step.

``failure_policy = "continue"`` — finalization is best-effort. The retro banner
UI is intentionally NOT printed here: it's a HITL-only ``RunSessionEnd`` side
effect (SubAgent has no TTY), fed from the funnel value above.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Mapping

from ...constants import ENV_SKIP_RETRO
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
            and os.environ.get(ENV_SKIP_RETRO) != "1"
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
