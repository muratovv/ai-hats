"""Worktree hook *carry collection* (HATS-823 D3; lifted from worktree_hooks
by ADR-0013 P1 / HATS-849).

The create-time chokepoint that composes a role and serializes the worktree
hooks it declares into a JSON-safe carry record, to be threaded into
``WorktreeManager.create(wt_hooks=...)`` and persisted to state for teardown.
This is an ai-hats accretion (it reaches Assembler / composer / materialize);
it lives outside the hook-agnostic worktree core. The hook *execution* policy
lives in :mod:`ai_hats.wt_lifecycle`; the bounded hook *run* primitive stays in
:mod:`ai_hats.worktree_hooks`.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def collect_carry_for_role(
    project_dir: Path, role: str = ""
) -> dict[str, list[dict[str, object]]]:
    """Compose the effective role and return its serialized worktree carry.

    The threading entry point (HATS-823 D3): the create-time caller
    (``state._setup_worktree`` / ``wt create`` CLI) calls this to collect the
    worktree hooks the worktree's role declares, to pass into
    ``WorktreeManager.create(wt_hooks=...)``. ``role`` falls back to the
    project's ``active_role`` / ``default_role`` (the canonical effective-role
    resolution). Compose failures degrade to an empty carry with a WARN —
    collection trouble must not block worktree creation.

    HATS-833 req-2 — create-time backstop: ``wt create`` runs via bare CLI /
    ``task transition execute`` / CI, where no session-start drift net fires, so
    the parent's ``library/wt-hooks/`` may be stale. We materialize the wt-hook
    scripts HERE (the carry-record chokepoint) and only keep a carry row whose
    backing script is present on disk afterwards — so the invariant **"recorded
    carry ⇒ backing script exists"** holds by construction, even for sessionless
    consumers. A declared hook whose source can't be resolved is dropped (with a
    WARN) rather than recorded-then-fail-closed at teardown. The fail-closed
    teardown (D7) stays as the last net for a genuinely vanished script.
    """
    from .assembler import Assembler
    from .hook_collection import collect_worktree_hooks
    from .materialize import compose_for_role

    try:
        assembler = Assembler(project_dir=project_dir)
        cfg = assembler.project_config
        effective = role or cfg.active_role or cfg.default_role
        if not effective:
            return {}
        result = compose_for_role(assembler, effective)
        carry = serialize_collected_hooks(collect_worktree_hooks(result))
        if carry:
            # Materialize MUST complete before we keep the carry. If it raises,
            # the outer except drops the whole carry ({}); a partial/unresolvable
            # script is then filtered out below. Never return a carry row that
            # lacks a backing script (review pt-4: degrade-to-empty is safe,
            # degrade-to-partial re-opens the fail-closed it prevents).
            assembler.hooks.materialize_worktree_hooks(result)
            carry = _drop_unbacked_carry_rows(carry, project_dir)
        return carry
    except Exception as exc:  # noqa: BLE001 — never block create on carry collection
        logger.warning(
            "worktree hooks: could not compose/materialize role %r for carry "
            "collection: %s — dropping carry",
            role,
            exc,
        )
        return {}


def _drop_unbacked_carry_rows(
    carry: dict[str, list[dict[str, object]]], project_dir: Path
) -> dict[str, list[dict[str, object]]]:
    """Keep only carry rows whose materialized script exists on disk (HATS-833).
    A row with no backing script (unresolvable source) is dropped with a WARN —
    enforcing "recorded carry ⇒ backing script exists" by construction."""
    from .paths import managed_wt_hook_filename, wt_hooks_dir

    wt_dir = wt_hooks_dir(project_dir)
    out: dict[str, list[dict[str, object]]] = {}
    for kind, rows in carry.items():
        kept: list[dict[str, object]] = []
        for row in rows:
            dest = wt_dir / managed_wt_hook_filename(str(row["skill"]), str(row["script"]))
            if dest.is_file():
                kept.append(row)
            else:
                logger.warning(
                    "worktree hooks: dropping carry row %s/%s — no backing script "
                    "on disk after materialize (%s)",
                    row["skill"],
                    row["script"],
                    dest,
                )
        if kept:
            out[kind] = kept
    return out


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
