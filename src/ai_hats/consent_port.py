"""The seam between ai-hats and the consent gate — a leaf, on purpose (HATS-1735).

A leaf because ``wt_effects`` may not import ``rack_wiring`` (that edge is a
cycle, spelled out at ``wt_effects.py:23-25``), and both of them plus the
PreToolUse guard have to ask the same question.

The answer is a four-valued :class:`Verdict`, never a bool: collapsing "no
session, so I could not look" into "refused" is what ADR-0029 D6 forbids, and
the collapse would happen right here if this returned ``True``/``False``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
    return tuple(
        str(row.get("selector", ""))
        for row in rows
        if isinstance(row, dict) and row.get("app") == APP and row.get("selector")
    )


def verdict(op: Operation, *, target_dir: Path | None) -> Verdict:
    """Does a live grant cover ``op``?

    ``target_dir`` is the project anchor ALREADY RESOLVED by the caller — rack
    hands its ``RackRoot.project_dir``, ``wt_effects`` its own. Measured
    (HATS-1735): re-resolving here would answer ``/Users/<me>`` for a linked
    worktree, because ai-hats's own resolver finds a home-level ``.agent/``
    before it reaches the gitlink hop.
    """
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


def note(answer: Verdict, op: Operation, *, hook: str) -> str:
    """Record a move a grant paid for, and return the work-log line for it.

    Journalling lives here rather than at each call site for the reason
    ``env_consent_note`` already gives: one wording for every record beats two
    copies that drift. Without the line, "the grant covered it" and "nobody was
    ever asked" read the same afterwards (ADR-0029 D11).
    """
    import sys

    from ai_hats_library.hooks.bypass_journal import journal_bypass

    label = op.label or op.type
    journal_bypass(
        "hatch",
        f"consent grant {answer.grant_id[:8]} ({op.type})",
        hook=hook,
        cmd=" ".join(sys.argv),
    )
    return f"{label}: supervisor consent grant accepted (grant {answer.grant_id[:8]})"


__all__ = [
    "APP",
    "RACK_TRANSITION",
    "WT_MERGE",
    "Operation",
    "Outcome",
    "Verdict",
    "declared_types",
    "note",
    "verdict",
]
