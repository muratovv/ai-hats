"""Worktree hook *carry collection* (HATS-823 D3; lifted from worktree_hooks
by ADR-0013 P1 / HATS-849).

The create-time chokepoint that serializes a composed role's worktree hooks
into a JSON-safe carry record, to be threaded into
``WorktreeManager.create(wt_hooks=...)`` and persisted to state for teardown.
HATS-865: composition happens at the integrator callers
(``wt_effects.collect_carry_for_project`` / ``wt create`` CLI) — this brick
receives the READY result and never imports the composition layer. The hook
*execution* policy lives in :mod:`ai_hats.wt_lifecycle`; the bounded hook *run*
primitive stays in :mod:`ai_hats.worktree_hooks`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult

logger = logging.getLogger(__name__)


def collect_carry_for_role(
    result: "CompositionResult | None",
) -> dict[str, list[dict[str, object]]]:
    """Serialize the composed role's worktree carry, dropping unresolvable rows.

    ``None`` degrades to an empty carry, as does a serialize failure (with a
    WARN) — collection trouble must not block worktree creation.

    HATS-1269 retired the HATS-833 materialize backstop: scripts spawn in place
    from the declaring skill, so what a recorded row now promises is **"its
    script resolved at create time"**. A typo in ``SKILL.md`` is dropped here
    with a WARN instead of surfacing days later as a blocked merge.

    HATS-1592: a composition carrying ``result.errors`` still yields its carry,
    with a WARN — zero skills and one dropped skill both read as "declares no
    hooks" from here. Never a refusal or a degrade-to-empty: an overlay typo
    composes fully today, so dropping the carry on *any* error would cause the
    very loss this record exists to prevent.
    """  # comment-length: allow — why this warns instead of degrading IS the contract
    if result is None:
        return {}
    from .hook_collection import collect_worktree_hooks, resolve_skill_script

    try:
        carry = serialize_collected_hooks(
            {
                kind: [
                    (skill, hook)
                    for skill, hook in entries
                    if _keep_row(result, skill, hook, resolve_skill_script)
                ]
                for kind, entries in collect_worktree_hooks(result).items()
            }
        )
    except Exception as exc:  # noqa: BLE001 — never block create on carry collection
        logger.warning(
            "worktree hooks: could not serialize carry for role %r: %s — dropping carry",
            result.name,
            exc,
        )
        return {}
    if result.errors:
        logger.warning(
            "worktree carry: role %r composed with errors, so its carry may be "
            "incomplete — %d hook row(s) recorded; worktree data whose wt_out hook "
            "is missing will be destroyed at teardown without further warning. %s",
            result.name,
            sum(len(rows) for rows in carry.values()),
            "; ".join(str(e) for e in result.errors),
        )
    return carry


def _keep_row(result, skill_name: str, hook, resolve) -> bool:
    if resolve(result, skill_name, hook.script) is not None:
        return True
    logger.warning(
        "worktree hooks: dropping carry row %s/%s — the declaring skill ships no such script",
        skill_name,
        hook.script,
    )
    return False


def serialize_collected_hooks(
    collected: dict[str, list[tuple[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    """Flatten ``collect_worktree_hooks`` output into a JSON-safe carry record.

    ``{kind: [(skill, WorktreeHook)]}`` → ``{kind: [{skill, script, on?}]}`` —
    the shape persisted in worktree state and consumed by the lifecycle bundle's
    run methods at create / teardown (HATS-823). ``on`` is omitted for ``wt_in``
    (always empty) and for any leaf with an empty ``on``.
    """
    out: dict[str, list[dict[str, object]]] = {}
    for kind, entries in collected.items():
        rows: list[dict[str, object]] = []
        for skill_name, hook in entries:
            row: dict[str, object] = {"skill": skill_name, "script": hook.script}
            if getattr(hook, "on", ()):  # wt_out carries teardown events
                row["on"] = list(hook.on)
            rows.append(row)
        if rows:
            out[kind] = rows
    return out
