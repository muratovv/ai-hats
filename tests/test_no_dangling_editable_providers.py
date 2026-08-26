"""No installed provider entry point may be dangling (HATS-966).

Prevents the whole HATS-965/966 class. A provider plugin whose editable
``.pth`` target was deleted (torn-down worktree, moved checkout) survives in
dist metadata — discovery still lists it — but its module won't import, so it
silently drops from the registry and ``-p <plugin>`` errors as "unknown". This
asserts, in whatever environment runs it, that:

1. every advertised provider entry-point module RESOLVES (``find_spec``), and
2. discovery and the live registry AGREE — nothing discovered is silently
   dropped (``surface_names()`` covers every entry-point name).

Fail-under-regress: reintroduce a dangling editable (or a provider plugin with a
broken import) into the installed venv → this turns red.

HATS-1493 moved it out of ``tests/e2e/``: it never spawned a process, it probes
the test interpreter's own installed distributions. It consequently left the
merge-smoke cohort and now runs in ``ci_unit`` across the py3.11/3.12/3.13
matrix — three interpreters per push instead of one venv at merge.

Deliberate contract docstring — noqa: comment-length.
"""

from __future__ import annotations

from ai_hats.surface_registry import surface_names
from ai_hats.self_heal import _provider_entry_points, find_broken_surface_providers


def test_no_installed_provider_is_dangling() -> None:
    broken = find_broken_surface_providers()
    assert broken == [], (
        "installed provider entry point(s) do not resolve — a dangling editable "
        f"shipped: {[(b.ep_name, b.module) for b in broken]}"
    )


def test_discovery_and_registry_agree() -> None:
    discovered = {ep.name for ep in _provider_entry_points()}
    registered = set(surface_names())
    silently_dropped = discovered - registered
    assert not silently_dropped, (
        f"provider(s) discovered but not registered (failed to load): {sorted(silently_dropped)}"
    )
