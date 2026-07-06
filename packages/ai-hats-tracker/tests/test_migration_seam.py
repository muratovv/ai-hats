"""Migration-seam test (HATS-933).

0.1.0 ships the seam only: an EMPTY registry wired to the generic
``ai_hats_core.migrations`` runner. Proves the wiring is callable and no-ops
until real TaskCard-schema migrations land (a later increment).
"""

from __future__ import annotations

from ai_hats_tracker import migrations


def test_registry_is_empty_for_0_1_0():
    assert migrations.TRACKER_MIGRATIONS == []


def test_run_pending_no_ops_on_empty_registry():
    assert migrations.run_pending(object()) == 0
