"""``run_session_end`` step — auto-retro decision + spawn + SESSION_END hooks.

Final stage of the ``finalize-hitl`` sub-pipeline (HATS-535). Runs after
``make_audit`` so hooks and the auto-retro reviewer can read the
structured ``audit.md`` (👤/👾/🔧/💭) instead of the incremental
skeleton.

Pre-refactor this logic lived inline in ``runtime._finalize_session`` —
extraction here serves three goals: (1) test seam (instantiate
``RunSessionEnd`` with a known session-dir + hooks_env), (2)
pipeline-level visibility (``finalize-hitl.yaml`` shows the lifecycle
explicitly), (3) future SubAgent parity is a one-line YAML change if
ever desired (currently SubAgent's ``finalize-subagent.yaml`` omits
this step to preserve pre-HATS-535 behaviour).

Three sub-phases, each wrapped in ``try/except (Exception,
KeyboardInterrupt)`` per the HATS-086 invariant — a second Ctrl+C must
not kill cleanup partway:

1. **Retro decision** — pure ``make_decision(project_dir, session_id)``
   + ``write_retro_log`` so the decision survives even if the spawn
   or hooks crash.
2. **Session-reviewer spawn** — when ``retro.action == "run"`` AND not
   recursion-guarded by ``HATS_SKIP_RETRO``, fire
   ``_spawn_session_reviewer_background``.
3. **SESSION_END hooks** — ``HooksRunner(hooks_dir, project_dir).run(
   SESSION_END, env=hooks_env)``; reconstructs ``HooksRunner`` from
   ``project_dir`` rather than threading the runner instance through
   the funnel (stateless class, cheap to rebuild).

Plus a **retro reminder banner** at the tail (the cyan "Reflect through
N sessions" + wrap-up nudge lines). Pre-refactor these printed inline
inside ``_print_session_end`` BEFORE this step's logic ran; they now
print AFTER hooks fire, which preserves visual ordering since
``_print_session_end`` already fired in the ``Provider``'s ``finally``
before the finalize pipeline ran.

``failure_policy = "continue"`` — same rationale as ``make_audit``:
finalization is best-effort; the pipeline runner stays oblivious to
per-step failures (they get logged).
"""

from __future__ import annotations

import logging
import os
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
        **_: Any,
    ) -> dict[str, Any]:
        from ...models import LifecycleEvent
        from ...paths import hooks_dir as _hooks_dir
        from ...retro.auto_retro import (
            _spawn_session_reviewer_background,
            make_decision,
            write_retro_log,
        )
        from ...runtime import HooksRunner

        del session_dir, exit_code, audit_path  # contract-required; consumed implicitly by hooks/retro readers

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
