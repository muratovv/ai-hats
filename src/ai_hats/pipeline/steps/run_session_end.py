"""``run_session_end`` step — retro reminder banner.

Final HITL-only stage of the ``finalize-hitl`` sub-pipeline. Runs
after ``make_audit`` and ``maybe_spawn_session_reviewer`` so the banner
prints the retro decision already taken by the upstream step.

History: this step used to own the auto-retro decision/spawn
block; that logic was extracted into ``maybe_spawn_session_reviewer``.
This step then also dispatched SESSION_END lifecycle hooks via
``HooksRunner`` — but that channel had zero real consumers (the
``hooks:`` composition channel was never executed and was later deleted),
so dispatch was removed. What remains is the cyan retro reminder banner.
SubAgent's ``finalize-subagent`` pipeline does NOT include this step
(no TTY for the banner).

The single sub-phase is wrapped in ``try/except (Exception,
KeyboardInterrupt)`` per the interrupt-safety invariant — a second Ctrl+C must
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
            # ``retro_decision`` is produced by
            # ``maybe_spawn_session_reviewer`` upstream. It's optional
            # so a finalize pipeline that skips that step (or where the
            # decision crashed) still runs cleanly — the banner is just
            # silently skipped.
            optional=frozenset({"retro_decision", "session_dir"}),
            produces=frozenset(),
        )

    def run(
        self,
        *,
        retro_decision: dict | None = None,
        session_dir: Any = None,
        **_: Any,
    ) -> dict[str, Any]:
        if retro_decision is not None:
            try:
                from ...startup_notices import save_session_diagnostics

                save_session_diagnostics(session_dir, "retro_reminder", retro_decision)
                _print_retro_banner(retro_decision)
            except (Exception, KeyboardInterrupt):
                logger.warning("retro banner failed", exc_info=True)

        return {}


def _print_retro_banner(retro: dict) -> None:
    """Render the cyan retro reminder.

    The decision's ``wrap_up`` payload is deliberately not rendered here: it
    is advice for a live session, shown after the session has already exited.
    It stays in ``diagnostics.json`` for ``session show``.
    """
    rem = retro.get("reminder")
    if rem:
        print(f"\033[33m  Reflect the project through {rem['count']} sessions:\033[0m")
        print(f"     \033[36m{rem['command']}\033[0m")
