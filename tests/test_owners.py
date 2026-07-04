"""Owner registry: living mechanisms that materialize files outside <ai_hats_dir>.

HATS-905 phase 1: the sweeper's liveness predicate — a marker whose owner_key
is not registered belongs to a dead mechanism.
"""

from __future__ import annotations

import pytest

from ai_hats import owners


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = owners.registered()
    owners._reset_for_tests()
    yield
    owners._reset_for_tests()
    for key, module in snapshot.items():
        owners.register_owner(key, module=module)


def test_registered_owner_is_living():
    owners.register_owner("git-hooks", module="ai_hats.hooks_manager")

    assert owners.is_living("git-hooks")
    assert "git-hooks" in owners.living_owners()


def test_unknown_owner_is_not_living():
    assert not owners.is_living("retired-mech")
    assert owners.living_owners() == frozenset()


def test_duplicate_registration_is_an_error():
    owners.register_owner("git-hooks", module="ai_hats.hooks_manager")

    with pytest.raises(owners.OwnerRegistryError):
        owners.register_owner("git-hooks", module="elsewhere")


def test_registered_exposes_key_to_module_mapping():
    owners.register_owner("runtime-hooks", module="ai_hats.providers")

    assert owners.registered() == {"runtime-hooks": "ai_hats.providers"}
    # a copy, not the live dict
    owners.registered().clear()
    assert owners.is_living("runtime-hooks")


def test_reset_clears_registry():
    owners.register_owner("git-hooks", module="ai_hats.hooks_manager")
    owners._reset_for_tests()

    assert owners.living_owners() == frozenset()
