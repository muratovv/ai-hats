"""``maybe_spawn_session_reviewer`` step — auto-retro decision + spawn.

Single source of truth for the auto-retro spawn block, shared by `finalize-hitl`
(WrapRunner) and `finalize-subagent` (SubAgentRunner) sub-pipelines (HATS-530,
which closed the prior HITL-only asymmetry).

Three sub-phases, each wrapped in ``try/except (Exception, KeyboardInterrupt)``
per the HATS-086 invariant (a second Ctrl+C during cleanup must not propagate):

1. **Retro decision** — ``make_decision`` + ``write_retro_log`` so the decision
   survives even if the spawn crashes.
2. **Spawn** — when ``retro.action == "run"`` and not ``HATS_SKIP_RETRO=1``: fire
   ``_spawn_session_reviewer_background`` (default), or run synchronously
   in-process (HATS-1402) when ``retro.background is False``.
3. **Return delta** — emit ``retro_decision`` for a downstream banner step.

``failure_policy = "continue"`` — finalization is best-effort. The retro banner
UI is intentionally NOT printed here: it's a HITL-only ``RunSessionEnd`` side
effect (SubAgent has no TTY), fed from the funnel value above.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ai_hats_core.layout import ProjectLayout
from typing import Any, Mapping

from ...constants import ENV_SKIP_RETRO
from ..step import Step, StepIO

logger = logging.getLogger(__name__)


def _write_outcome(project_dir: Path, session_id: str, detail: str) -> None:
    """HATS-1487: the decision line records intent; this records what happened."""
    from ...retro.auto_retro import write_retro_log

    write_retro_log(project_dir, session_id, "runtime", "outcome", detail)


class MaybeSpawnSessionReviewer(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="maybe_spawn_session_reviewer",
            requires=frozenset({"session_id", "layout"}),
            produces=frozenset({"retro_decision"}),
        )

    def run(
        self,
        *,
        session_id: str,
        layout: ProjectLayout,
        **_: Any,
    ) -> dict[str, Any]:
        project_dir = layout.root
        from ...cli import reflect_session_main
        from ...retro.auto_retro import (
            _spawn_session_reviewer_background,
            make_decision,
            write_retro_log,
        )

        retro_decision: dict | None = None
        try:
            # HATS-1426: the breadcrumb lands BEFORE the decision — the incident
            # died inside make_decision and left no retro.log at all.
            write_retro_log(project_dir, session_id, "runtime", "start", "deciding")
            retro_decision = make_decision(project_dir, session_id)
            write_retro_log(
                project_dir,
                session_id,
                "runtime",
                "decision",
                f"{retro_decision['action']}: {retro_decision['reason']}",
            )
        except (Exception, KeyboardInterrupt):
            logger.warning("retro decision/log failed", exc_info=True)

        if retro_decision is not None and retro_decision.get("action") == "run":
            guard = os.environ.get(ENV_SKIP_RETRO)
            observed = f"{ENV_SKIP_RETRO}={guard!r}"
            if guard == "1":
                _write_outcome(project_dir, session_id, f"suppressed-by-guard ({observed})")
            elif retro_decision.get("background") is False:
                # HATS-1402: sync in-process run; recursion guard scoped via
                # try/finally since there's no child process to scope it to.
                _write_outcome(project_dir, session_id, f"sync-start ({observed})")
                try:
                    os.environ[ENV_SKIP_RETRO] = "1"
                    # Boundary adapter: the sync branch mirrors what the
                    # subprocess main would do — deserialize the session's own
                    # project (R6) instead of re-deriving retros from a Path.
                    from ai_hats.cli._entry import resolve_project

                    rc = reflect_session_main.run_session_review(
                        session_id, 1, resolve_project().layout
                    )
                    _write_outcome(project_dir, session_id, f"sync-done (rc={rc})")
                except (Exception, KeyboardInterrupt) as exc:
                    logger.warning(
                        "session-reviewer sync run failed",
                        exc_info=True,
                    )
                    _write_outcome(project_dir, session_id, f"sync-failed ({exc!r})")
                finally:
                    os.environ.pop(ENV_SKIP_RETRO, None)
            else:
                _write_outcome(project_dir, session_id, f"spawn-bg ({observed})")
                try:
                    _spawn_session_reviewer_background(project_dir, session_id)
                except (Exception, KeyboardInterrupt):
                    logger.warning(
                        "session-reviewer spawn failed",
                        exc_info=True,
                    )

        if retro_decision is not None:
            return {"retro_decision": retro_decision}
        return {}
