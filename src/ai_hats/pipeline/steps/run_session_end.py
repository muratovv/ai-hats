"""``run_session_end`` step — SESSION_END hooks + retro reminder banner.

Final HITL-only stage of the ``finalize-hitl`` sub-pipeline. Runs
after ``make_audit`` and ``maybe_spawn_session_reviewer`` so hooks
see the structured ``audit.md`` (👤/👾/🔧/💭) AND so the banner
prints the retro decision already taken by the upstream step.

Pre-HATS-530 this step also owned the auto-retro decision/spawn
block. HATS-530 extracted that block into
``maybe_spawn_session_reviewer`` so the SubAgent pipeline can share
it; what remains here is HITL-specific: SESSION_END hooks dispatch
and the cyan retro reminder banner. SubAgent's ``finalize-subagent``
pipeline does NOT include this step — it intentionally omits
SESSION_END hooks (pre-HATS-535 contract) and has no TTY for the
banner.

Two sub-phases, each wrapped in ``try/except (Exception,
KeyboardInterrupt)`` per the HATS-086 invariant — a second Ctrl+C
must not kill cleanup partway:

1. **SESSION_END hooks** — ``HooksRunner(hooks_dir, project_dir).run(
   SESSION_END, env=hooks_env)``; reconstructs ``HooksRunner`` from
   ``project_dir`` rather than threading the runner instance through
   the funnel (stateless class, cheap to rebuild).
2. **Retro reminder banner** — the cyan "Reflect through N sessions"
   + wrap-up nudge lines. Reads ``retro_decision`` (optional input)
   produced by ``maybe_spawn_session_reviewer``; absent
   ``retro_decision`` → no banner (silent no-op).

``failure_policy = "continue"`` — finalization is best-effort.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
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
            requires=frozenset({
                "session_id", "session_dir", "project_dir",
                "exit_code", "audit_path", "hooks_env",
            }),
            # HATS-530: ``retro_decision`` is produced by
            # ``maybe_spawn_session_reviewer`` upstream. It's optional
            # so a finalize pipeline that skips that step (or where
            # the decision crashed) still runs hooks cleanly — the
            # banner just gets silently skipped.
            optional=frozenset({"retro_decision"}),
            produces=frozenset(),
        )

    def run(
        self,
        *,
        session_id: str,
        session_dir: Path,
        project_dir: Path,
        exit_code: int,
        audit_path: Path,
        hooks_env: dict[str, str],
        retro_decision: dict | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        from ...models import LifecycleEvent
        from ...paths import hooks_dir as _hooks_dir
        from ...runtime import HooksRunner

        del session_id, session_dir, exit_code, audit_path  # contract-required; consumed implicitly by hooks readers

        try:
            hooks_runner = HooksRunner(_hooks_dir(project_dir), project_dir)
            hook_results = hooks_runner.run(
                LifecycleEvent.SESSION_END, env=hooks_env,
            )
            for hr in hook_results:
                if hr.get("stderr"):
                    print(hr["stderr"], end="", file=sys.stderr)
        except (Exception, KeyboardInterrupt):
            logger.warning("session_end hook failed", exc_info=True)

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
