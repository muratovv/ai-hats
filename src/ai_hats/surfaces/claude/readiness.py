"""The claude readiness probe — ``claude auth status`` before a run takes anything."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Mapping

from ai_hats_observe.canonical.signals import (
    Notice,
    PersonActionRequired,
    PersonMustAct,
    Signal,
    WorthRecording,
)
from ai_hats_observe.canonical.types import now

SOURCE = "claude/readiness"
NOT_AUTHENTICATED = "Claude is not authenticated. Run `claude auth login`, then retry ai-hats."
_PROBE_TIMEOUT_S = 10


def readiness_findings(
    environ: Mapping[str, str],
    *,
    binary: str = "claude",
    which=shutil.which,
    run=subprocess.run,
) -> list[Signal]:
    """Refuse only on a parsed ``loggedIn: false``; anything the probe cannot
    read is a ``Notice`` — a wrong refusal costs more than a late failure.
    ``binary`` is what the surface launches, so a surface that swaps its CLI
    probes the swap."""
    resolved = which(binary, path=environ.get("PATH"))
    if not resolved:
        return [_notice(f"{binary} is not on PATH; auth not probed")]
    binary = resolved
    try:
        probe = run(
            [binary, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            env=dict(environ),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [
            _notice(f"`claude auth status` did not run ({type(exc).__name__}); auth not probed")
        ]
    status = _parse(probe.stdout)
    if status is None:
        return [_notice("`claude auth status` returned no JSON (older CLI?); auth not probed")]
    if status.get("loggedIn") is False:
        method = status.get("authMethod")
        return [
            PersonActionRequired(
                reason=PersonMustAct.REAUTHENTICATE,
                detail=NOT_AUTHENTICATED,
                raw_code=method if isinstance(method, str) else None,
                source=SOURCE,
                ts=now(),
            )
        ]
    return []


def _parse(stdout: str) -> dict | None:
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _notice(detail: str) -> Notice:
    return Notice(reason=WorthRecording.SURFACE_WARNING, detail=detail, source=SOURCE, ts=now())


__all__ = ["NOT_AUTHENTICATED", "SOURCE", "readiness_findings"]
