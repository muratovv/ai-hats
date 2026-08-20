"""Ai-hats host seam used only by the external session consent wrapper."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Mapping

from ai_hats_library.hooks.consent_gate import Operation, Outcome, Verdict, check, store_root_from

logger = logging.getLogger(__name__)

#: This integration's app key. ``check_points.KNOWN_APPS`` spells it too, and
#: ``test_the_app_roster_matches_the_integrations_that_claim_the_keys`` is what
#: keeps the two from drifting apart in silence.
APP = "consent_gate"

#: Operation types, as the role declares them under ``apps.consent_gate``.
RACK_TRANSITION = "rack.transition"
WT_MERGE = "wt.merge"


def declared_types(session_dir: Path) -> tuple[str, ...]:
    """Operation types this session's role declared as gateable.

    Read from the launch-frozen ``role_materialization.json``, the same file the
    stdlib guard reads — one source, so the two readers cannot disagree about
    what is gated.
    """
    try:
        report = json.loads(
            (Path(session_dir) / "role_materialization.json").read_text(encoding="utf-8")
        )
        rows = report.get("consent") or []
    except (OSError, ValueError) as exc:
        logger.warning("consent declaration unreadable at %s: %r", session_dir, exc)
        return ()
    declared: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = row.get("path")
        if row.get("app") != APP or not isinstance(path, list) or not path:
            continue
        operation = str(path[0])
        if operation and operation not in declared:
            declared.append(operation)
    return tuple(declared)


def verdict(op: Operation, *, target_dir: Path | None) -> Verdict:
    """Return the four-valued grant verdict for the wrapper's project anchor."""
    from .session_identity import SessionIdentity, SessionIdentityError

    try:
        identity = SessionIdentity.from_env()
    except SessionIdentityError as exc:
        return Verdict(Outcome.NO_AGENT, f"the session envelope is unreadable: {exc}")
    if identity is None:
        return Verdict(Outcome.NO_AGENT, "not inside an ai-hats session")
    return check(
        op,
        session_id=identity.id,
        store_root=store_root_from(identity.session_cache_dir),
        project_dir=target_dir,
        policy=declared_types(identity.session_dir),
    )


def journal_sink(hook: str, project_dir: Path) -> Callable[[Mapping[str, object]], bool]:
    """Bind the engine's record callback to this project's bypass journal."""
    import sys

    from ai_hats_library.hooks.bypass_journal import journal_bypass

    def _write(entry: Mapping[str, object]) -> bool:
        return journal_bypass(
            "hatch",
            journal_reason(entry),
            hook=hook,
            cmd=" ".join(sys.argv),
            cwd=project_dir,
        )

    return _write


def journal_reason(entry: Mapping[str, object]) -> str:
    """One greppable line: which grant, which operation, how wide, how much left.

    Kept in ``reason`` rather than widened into ``bypass_journal.FIELDS``: that
    schema is shared with every git hook, so a new column would rewrite the shape
    of every line in the journal to serve one of them.
    """
    radius = ",".join(str(item) for item in (entry.get("radius") or ()))
    left = int(entry.get("left_s") or 0) // 60
    window = int(entry.get("window_s") or 0) // 60
    return (
        f"consent grant {str(entry.get('grant_id') or '')[:8]} ({entry.get('op')}) "
        f"radius=[{radius}] window={left}m/{window}m outcome={entry.get('outcome')}"
    )


def record_use(answer: Verdict, op: Operation, *, hook: str, project_dir: Path) -> bool:
    """Record one authorization use in the wrapper's project journal."""
    from ai_hats_library.hooks.consent_gate import record

    return record(answer, op, journal=journal_sink(hook, project_dir))


__all__ = [
    "APP",
    "RACK_TRANSITION",
    "WT_MERGE",
    "Operation",
    "Outcome",
    "Verdict",
    "declared_types",
    "journal_reason",
    "journal_sink",
    "record_use",
    "verdict",
]
