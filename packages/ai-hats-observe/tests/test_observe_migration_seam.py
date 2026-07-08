"""Migration-seam test (HATS-948, T15).

0.1.0 ships the seam only: an EMPTY registry wired to the generic
``ai_hats_core.migrations`` runner. Proves the wiring is callable and no-ops
until a real audit-schema migration (an ``audit/v2`` bump) lands.
"""

from __future__ import annotations

from ai_hats_observe import migrations


def test_registry_is_empty_for_0_1_0():
    assert migrations.OBSERVE_MIGRATIONS == []


def test_run_pending_no_ops_on_empty_registry():
    assert migrations.run_pending(object()) == 0
