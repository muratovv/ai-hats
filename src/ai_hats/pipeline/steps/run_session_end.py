"""``run_session_end`` step — retro reminder banner.

Final HITL-only stage of the ``finalize-hitl`` sub-pipeline. Runs
after ``make_audit`` and ``maybe_spawn_session_reviewer`` so the banner
prints the retro decision already taken by the upstream step.

History: pre-HATS-530 this step owned the auto-retro decision/spawn
block; HATS-530 extracted that into ``maybe_spawn_session_reviewer``.
This step then also dispatched SESSION_END lifecycle hooks via
``HooksRunner`` — but that channel had zero real consumers (the
``hooks:`` composition channel was never executed; HATS-707 deleted it),
so dispatch was removed. What remains is the cyan retro reminder banner.
SubAgent's ``finalize-subagent`` pipeline does NOT include this step
(no TTY for the banner).

The single sub-phase is wrapped in ``try/except (Exception,
KeyboardInterrupt)`` per the HATS-086 invariant — a second Ctrl+C must
not kill cleanup partway. Reads ``retro_decision`` (optional input)
produced by ``maybe_spawn_session_reviewer``; absent it → no banner
(silent no-op).

``failure_policy = "continue"`` — finalization is best-effort.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from ..step import Step, StepIO

logger = logging.getLogger(__name__)


class RunSessionEnd(Step):
    failure_policy = "continue"

    def __init__(self, params: Mapping[str, Any] | None = None) -> None:
        del params

    @property
    def io(self) -> StepIO:
        return StepIO(
            name="run_session_end",
            requires=frozenset(),
            # HATS-530: ``retro_decision`` is produced by
            # ``maybe_spawn_session_reviewer`` upstream. It's optional
            # so a finalize pipeline that skips that step (or where the
            # decision crashed) still runs cleanly — the banner is just
            # silently skipped.
            optional=frozenset({"retro_decision"}),
            produces=frozenset(),
        )

    def run(
        self,
        *,
        retro_decision: dict | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        if retro_decision is not None:
            try:
                _print_retro_banner(retro_decision)
            except (Exception, KeyboardInterrupt):
                logger.warning("retro banner failed", exc_info=True)

        return {}


def _print_retro_banner(retro: dict) -> None:
    """Render the cyan retro reminder + wrap-up nudge.

    Extracted verbatim from the pre-HATS-535 ``_print_session_end``
    body so behaviour is preserved modulo placement (now AFTER
    SESSION_END hooks rather than before).
    """
    rem = retro.get("reminder")
    if rem:
        print(
            f"\033[33m  Reflect the project through {rem['count']} sessions:\033[0m"
        )
        print(f"     \033[36m{rem['command']}\033[0m")

    wrap = retro.get("wrap_up")
    if wrap:
        print(
            f"\033[33m  Wrap up before next task — "
            f"{wrap['tasks_closed']} tasks closed in "
            f"{wrap['duration_min']}min, cache {wrap['cache_read_mb']}MB\033[0m"
        )
        print("     \033[36m/clear\033[0m before starting fresh work")
