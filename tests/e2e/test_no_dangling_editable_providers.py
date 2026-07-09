"""Gate smoke: no installed provider entry point may be dangling (HATS-966).

Prevents the whole HATS-965/966 class at the merge-to-master gate. A provider
plugin whose editable ``.pth`` target was deleted (torn-down worktree, moved
checkout) survives in dist metadata — discovery still lists it — but its module
won't import, so it silently drops from the registry and ``-p <plugin>`` errors
as "unknown". This asserts, in the freshly-built gate venv, that:

1. every advertised provider entry-point module RESOLVES (``find_spec``), and
2. discovery and the live registry AGREE — nothing discovered is silently
   dropped (``provider_names()`` covers every entry-point name).

Fail-under-regress: reintroduce a dangling editable (or a provider plugin with a
broken import) into the shipped venv → this turns red on the gate.

Deliberate gate-smoke contract — noqa: comment-length.
"""

from __future__ import annotations

import pytest

from ai_hats.providers import provider_names
from ai_hats.self_heal import _provider_entry_points, find_broken_surface_providers

# smoke: runs on the merge-to-master gate (HATS-783)
pytestmark = [pytest.mark.integration, pytest.mark.smoke]


def test_no_installed_provider_is_dangling() -> None:
    broken = find_broken_surface_providers()
    assert broken == [], (
        "installed provider entry point(s) do not resolve — a dangling editable "
        f"shipped: {[(b.ep_name, b.module) for b in broken]}"
    )


def test_discovery_and_registry_agree() -> None:
    discovered = {ep.name for ep in _provider_entry_points()}
    registered = set(provider_names())
    silently_dropped = discovered - registered
    assert not silently_dropped, (
        "provider(s) discovered but not registered (failed to load): "
        f"{sorted(silently_dropped)}"
    )
