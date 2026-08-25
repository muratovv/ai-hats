"""Provider-plugin entry-point discovery — a dependency-free leaf (HATS-978).

Discovery, not composition: hosting these in ``providers`` forced ``self_heal``
to import the composition layer (HATS-865/ADR-0014 violation). This leaf lets
``providers`` (registration) and ``self_heal`` (broken-editable detection) share
the primitive without depending on each other. Group = the HATS-870 IoC seam.
"""

from __future__ import annotations

import importlib.metadata

from typing import Any

PROVIDER_ENTRY_POINT_GROUP = "ai_hats.providers"


def _provider_entry_points():
    """Entry points advertised under the provider group (isolated for tests)."""
    return importlib.metadata.entry_points(group=PROVIDER_ENTRY_POINT_GROUP)


def _is_first_party_entry_point(ep: Any) -> bool:
    """Return True if entry point `ep` is shipped directly by first-party `ai-hats`.

    The test is the declaring distribution's name (`ai-hats` or `ai_hats`), not the
    entry-point name — so every surface ai-hats ships answers True, including the
    four folded in by HATS-1826, and a third-party plugin's dist answers False.
    Provider loading uses it to raise loudly on a first-party load failure
    (HATS-1121) instead of swallowing it as a warning line.
    """
    dist = getattr(ep, "dist", None)
    if dist is None:
        return False
    name = getattr(dist, "name", None)
    if not name and hasattr(dist, "metadata"):
        name = dist.metadata.get("Name")
    if not name:
        return False
    return name.replace("_", "-").lower() == "ai-hats"
