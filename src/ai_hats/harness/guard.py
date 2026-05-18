"""Post-run harness guard for reporting pipeline steps (HATS-378).

A pipeline step that spawns a sub-agent (interactive or otherwise) and
has been marked ``harness.reporting = true`` calls
:func:`apply_post_run_guard` after the run completes. The guard reads
the finalized metrics and raises a :class:`HarnessReliabilityError`
when the run is silently empty.

Timeout retry/escalation (HATS-321) lives in this module too — Phase 2
will land it; Phase 1 ships only the zero-output guard.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .diagnostic import diagnose_silent_session, is_zero_output
from .errors import HarnessZeroOutputError

if TYPE_CHECKING:
    from ..observe import Session
    from ..pipeline.harness_policy import HarnessPolicy


def apply_post_run_guard(
    session: "Session", policy: "HarnessPolicy | None",
) -> None:
    """Validate a finalized session against a step's reliability policy.

    No-op when ``policy`` is ``None`` or ``policy.reporting`` is false.
    Skips guard for runs that did not exit cleanly (non-zero exit code or
    ``timed_out=True``) — those have their own failure paths.

    Raises:
        HarnessZeroOutputError: when ``policy.reporting`` is true,
            ``on_zero_output`` is not ``"ignore"``, and metrics report
            zero output tokens AND zero tool calls. Caller is expected
            to translate this into a meta-PROP under
            ``target=harness-incident`` (Phase 3).
    """
    if policy is None or not policy.reporting:
        return
    if policy.on_zero_output == "ignore":
        return
    if not session.metrics_path.exists():
        return
    try:
        metrics = json.loads(session.metrics_path.read_text())
    except (OSError, ValueError):
        return
    if metrics.get("exit_code", 0) != 0:
        return
    if metrics.get("timed_out"):
        return
    if is_zero_output(metrics):
        raise HarnessZeroOutputError(
            session.session_id, diagnose_silent_session(session),
        )
