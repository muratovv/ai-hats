"""Provider-plugin entry-point discovery — a dependency-free leaf (HATS-978).

Discovery, not composition: hosting these in ``providers`` forced ``self_heal``
to import the composition layer (HATS-865/ADR-0014 violation). This leaf lets
``providers`` (registration) and ``self_heal`` (broken-editable detection) share
the primitive without depending on each other. Group = the HATS-870 IoC seam.
"""

from __future__ import annotations

import importlib.metadata

PROVIDER_ENTRY_POINT_GROUP = "ai_hats.providers"


def _provider_entry_points():
    """Entry points advertised under the provider group (isolated for tests)."""
    return importlib.metadata.entry_points(group=PROVIDER_ENTRY_POINT_GROUP)
