"""Worktree lifecycle-hook carry schema (HATS-823, ADR-0012; moved from
``ai_hats.models`` by HATS-863 — ADR-0014 §2: the wt package owns its schema).

Cross-package payloads ride opaque (a skill's ``worktree:`` frontmatter block
reaches the integrator as a raw dict); :func:`parse_worktree_carry` is the one
typed entry point the integrator calls at its compose-time chokepoint. The
:class:`~ai_hats_wt.manager.WorktreeManager` itself stays hook-agnostic — its
``wt_hooks`` parameter remains a JSON-safe dict, never these types.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

from ai_hats_core import YamlModel
from pydantic import ConfigDict

WT_TEARDOWN_EVENTS: tuple[str, ...] = ("merge", "discard", "cleanup")


class WorktreeHook(YamlModel):
    """A single worktree lifecycle hook declared by a skill (HATS-823, ADR-0012).

    ``script`` is skill-dir-relative. ``on`` lists the teardown events a
    ``wt_out`` hook fires on (:func:`parse_worktree_carry` normalizes empty to
    *all* :data:`WT_TEARDOWN_EVENTS`); always empty for ``wt_in``.
    Frozen + ``extra="forbid"``: a malformed row is a silent data-loss hole (a
    ``wt_out`` drain that never runs), so the leaf fails loud.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str
    on: tuple[str, ...] = ()


class WorktreeCarry(YamlModel):
    """Worktree lifecycle hooks a skill declares (HATS-823, ADR-0012).

    The container is **forward-compatible**: unknown keys are ignored with a
    WARN at parse time (a newer skill declaring a future carry kind must not
    hard-fail composition on an older engine — ADR-0012 Revisions #3), in
    contrast to the fail-loud leaves. Frozen so collected carry is safe to pass
    around and persist into worktree state.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    wt_in: tuple[WorktreeHook, ...] = ()
    wt_out: tuple[WorktreeHook, ...] = ()

    def is_empty(self) -> bool:
        return not self.wt_in and not self.wt_out


def parse_worktree_carry(raw: Any, skill_name: str = "<unknown>") -> WorktreeCarry:
    """Parse + validate a skill's raw ``worktree:`` frontmatter block.

    Leaf rows fail loud (a dropped ``wt_out`` hook is the data-loss hole this
    mechanism closes); the *container* tolerates unknown keys with a WARN
    (forward-compat — ADR-0012 Revisions #3). ``wt_out`` ``on`` is validated
    against :data:`WT_TEARDOWN_EVENTS` and defaults to all routes when unset;
    ``wt_in`` ``on`` is meaningless (fires once at create) so it is dropped
    with a WARN.
    """
    if not raw:
        return WorktreeCarry()
    if not isinstance(raw, dict):
        raise ValueError(
            f"skill {skill_name!r}: worktree must be a mapping with "
            f"wt_in / wt_out, got {type(raw).__name__}"
        )
    known = {"wt_in", "wt_out"}
    unknown = [k for k in raw if k not in known]
    if unknown:
        warnings.warn(
            f"skill {skill_name!r}: unknown worktree carry key(s) "
            f"{', '.join(map(repr, unknown))} ignored (known: wt_in, wt_out)"
            f" — update ai-hats if this is a newer carry kind",
            stacklevel=2,
        )
    kinds: dict[str, tuple[WorktreeHook, ...]] = {}
    for kind in ("wt_in", "wt_out"):
        rows = raw.get(kind)
        if rows is None:
            continue
        if not isinstance(rows, list):
            raise ValueError(
                f"skill {skill_name!r}: worktree[{kind!r}] must be a list of "
                f"{{script, on?}} entries, got {type(rows).__name__}"
            )
        kinds[kind] = tuple(_parse_hook(kind, row, skill_name) for row in rows)

    carry = WorktreeCarry(wt_in=kinds.get("wt_in", ()), wt_out=kinds.get("wt_out", ()))
    _reject_basename_collisions((*carry.wt_in, *carry.wt_out), skill_name)
    return carry


def _parse_hook(kind: str, row: Any, skill_name: str) -> WorktreeHook:
    """Parse one ``{script, on?}`` row into a validated :class:`WorktreeHook`."""
    if isinstance(row, dict) and True in row and "on" not in row:
        # YAML 1.1 parses bare `on:` as boolean True — restore it.
        row = {("on" if k is True else k): v for k, v in row.items()}
    match row:
        case {"script": script, "on": list() as on_raw}:
            on = tuple(str(e) for e in on_raw)
        case {"script": script, "on": bad}:
            raise ValueError(
                f"skill {skill_name!r}: worktree[{kind!r}] 'on' must be a "
                f"list of teardown events, got {type(bad).__name__}"
            )
        case {"script": script}:
            on = ()
        case _:
            raise ValueError(
                f"skill {skill_name!r}: worktree[{kind!r}] entry must "
                f"have a 'script' — got {row!r}"
            )
    if kind == "wt_in":
        if on:
            warnings.warn(
                f"skill {skill_name!r}: worktree['wt_in'] entry has "
                f"'on' {list(on)} — ignored (wt_in fires once at create)",
                stacklevel=2,
            )
        on = ()
    else:  # wt_out
        bad_events = [e for e in on if e not in WT_TEARDOWN_EVENTS]
        if bad_events:
            raise ValueError(
                f"skill {skill_name!r}: worktree['wt_out'] 'on' has "
                f"unknown event(s) {bad_events} (allowed: "
                f"{', '.join(WT_TEARDOWN_EVENTS)})"
            )
        if not on:
            on = WT_TEARDOWN_EVENTS
    return WorktreeHook(script=str(script), on=on)


def _reject_basename_collisions(hooks: tuple[WorktreeHook, ...], skill_name: str) -> None:
    """Distinct scripts sharing a basename would silently overwrite each other
    on the ``<skill>-<basename>`` materialized filename; same script reused is fine."""
    basename_source: dict[str, str] = {}
    for hook in hooks:
        base = Path(hook.script).name
        prior = basename_source.get(base)
        if prior is not None and prior != hook.script:
            raise ValueError(
                f"skill {skill_name!r}: worktree scripts {prior!r} and "
                f"{hook.script!r} share basename {base!r} — they would "
                f"collide on the materialized filename; give them distinct "
                f"basenames"
            )
        basename_source[base] = hook.script
