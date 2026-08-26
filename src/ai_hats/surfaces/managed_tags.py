"""Removing ai-hats' own entries from a harness settings file (HATS-833).

A surface tags every settings entry it writes as ai-hats-owned; this is the other
half — drop the tags no longer wanted, leave what a person wrote alone.

Two readers is why it sits at the area's boundary and not inside one
implementation: a surface sweeping its own session, and ``ai_hats.sweeper``
doing the same for agy's pre-HATS-1166 remnant under a different tag key —
which until HATS-1826 it did by calling a private method past the contract.
"""

from __future__ import annotations

CLAUDE_TAG_KEY = "_ai_hats_managed"
CLAUDE_TAG_PREFIX = "ai-hats:"


def sweep_stale_managed_tags(
    hooks_root: dict,
    desired_tags: set[str],
    *,
    tag_key: str = CLAUDE_TAG_KEY,
    tag_prefix: str = CLAUDE_TAG_PREFIX,
) -> set[str]:
    """Drop managed entries outside ``desired_tags``; return the tags removed.

    Preserves user-authored entries and still-desired managed ones, and
    cascade-drops an event key whose list becomes empty. Mutates ``hooks_root``.
    """
    removed: set[str] = set()
    for event in list(hooks_root.keys()):
        event_list = hooks_root[event]
        if not isinstance(event_list, list):
            continue
        kept: list = []
        for entry in event_list:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get(tag_key), str)
                and entry[tag_key].startswith(tag_prefix)
                and entry[tag_key] not in desired_tags
            ):
                removed.add(entry[tag_key])
            else:
                kept.append(entry)
        if len(kept) != len(event_list):
            if kept:
                hooks_root[event] = kept
            else:
                del hooks_root[event]
    return removed
