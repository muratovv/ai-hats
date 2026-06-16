"""HATS-678 / HATS-771: deterministic guard for the install-heavy concurrency cap.

The pre-push e2e gate runs ``-n8 --dist=loadgroup``. ~26 tests across 21 files
do a real ``uv pip install`` at call time (``@pytest.mark.install_heavy``).
Under the pip engine, uncapped concurrency let up to ``nworkers`` (≤8) hit the
package index at once and intermittently reset (the flake class HATS-676
quarantined); ``tests/e2e/conftest.py`` caps them by round-robining their FILES
into ``INSTALL_HEAVY_GROUPS`` fixed xdist groups so ``loadgroup`` runs at most K
concurrently. HATS-771 relaxed the default K to 8 (uv serves ``--reinstall``
from its warm global cache, so the throttle is inert in the happy path) but
KEPT the grouping as a cold-network safety valve — so this scheduling contract
still matters.

This file is a PURE unit test — no real install, no integration marker — so it runs
in the normal fast suite and fails loudly if the cap regresses. The expensive
proof (a green ``-n8`` gate) lives in the gate run itself; this locks the
*scheduling contract* that makes the gate stable.

Fail-under-revert: drop the install-heavy branch from ``pytest_collection_modifyitems``
→ install-heavy items fall back to per-file groups (one group per file) →
``test_hook_routes_install_heavy_within_cap`` sees >K distinct groups and fails.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Import the e2e conftest by path — tests/e2e/ is not a package, so a plain
# ``import conftest`` is ambiguous with tests/conftest.py. Its only load-time
# side effect is a ``sys.path.insert(0, <tests/e2e>)`` — identical to (and
# idempotent with) pytest's own conftest load; the rest is fixture/hook defs
# and path math.
_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
_spec = importlib.util.spec_from_file_location("e2e_conftest_under_test", _CONFTEST)
_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conftest)

INSTALL_HEAVY_GROUPS = _conftest.INSTALL_HEAVY_GROUPS
_group_map = _conftest._install_heavy_group_map
_modifyitems = _conftest.pytest_collection_modifyitems


# --------------------------- pure helper ---------------------------


@pytest.mark.parametrize("n_files", [0, 1, 3, 4, 16, 50])
def test_group_map_never_exceeds_k(n_files: int) -> None:
    """Distinct groups == min(n_files, K) — never more than K concurrent."""
    files = [f"tests/e2e/test_install_{i}.py" for i in range(n_files)]
    mapping = _group_map(files, INSTALL_HEAVY_GROUPS)
    distinct = set(mapping.values())
    assert len(distinct) == min(n_files, INSTALL_HEAVY_GROUPS)
    assert len(distinct) <= INSTALL_HEAVY_GROUPS
    assert all(g.startswith("install_heavy_") for g in distinct)


def test_group_map_balanced_and_deterministic() -> None:
    """Round-robin spreads files evenly and is stable across calls (no clock)."""
    files = [f"f{i}.py" for i in range(16)]
    m1 = _group_map(files, INSTALL_HEAVY_GROUPS)
    m2 = _group_map(list(reversed(files)), INSTALL_HEAVY_GROUPS)
    assert m1 == m2, "assignment must depend only on the sorted file set"

    counts = {}
    for g in m1.values():
        counts[g] = counts.get(g, 0) + 1
    # 16 files / K groups → exactly 16//K each (perfectly balanced; 16 % K == 0
    # holds for the supported K ∈ {4, 8, 16}, which the default 8 satisfies).
    assert set(counts.values()) == {16 // INSTALL_HEAVY_GROUPS}


# --------------------------- hook routing ---------------------------


class _FakeItem:
    """Minimal stand-in for a pytest Item for the grouping hook."""

    def __init__(self, nodeid: str, *, install_heavy: bool = False, live: bool = False):
        self.nodeid = nodeid
        self.fixturenames = ("requires_claude_auth",) if live else ()
        self._has_install_heavy = install_heavy
        self.group: str | None = None
        self.markers: set[str] = set()

    def get_closest_marker(self, name: str):
        if name == "install_heavy" and self._has_install_heavy:
            return pytest.mark.install_heavy
        return None

    def add_marker(self, marker) -> None:
        if marker.name == "xdist_group":
            self.group = marker.args[0]
        else:
            self.markers.add(marker.name)


def test_hook_routes_install_heavy_within_cap() -> None:
    """The real hook caps install-heavy items at K groups and leaves the rest.

    This is the fail-under-revert guard: with the install-heavy branch removed each
    install-heavy file would get its own group → ``len(install_heavy_groups) > K``.
    """
    items = [
        _FakeItem(f"tests/e2e/test_ph_{i}.py::test_x", install_heavy=True)
        for i in range(10)
    ]
    items += [
        _FakeItem("tests/e2e/test_live.py::test_a", live=True),
        _FakeItem("tests/e2e/test_live.py::test_b", live=True),
        _FakeItem("tests/e2e/test_plain_one.py::test_a"),
        _FakeItem("tests/e2e/test_plain_two.py::test_a"),
    ]
    _modifyitems(None, items)

    install_groups = {it.group for it in items[:10]}
    assert len(install_groups) <= INSTALL_HEAVY_GROUPS, (
        f"install-heavy cap breached: {install_groups}"
    )
    assert all(g.startswith("install_heavy_") for g in install_groups)

    # live → single shared group; plain → own file group (unchanged contract).
    assert {it.group for it in items if it.fixturenames} == {"live_claude"}
    assert items[-2].group == "tests/e2e/test_plain_one.py"
    assert items[-1].group == "tests/e2e/test_plain_two.py"


def test_hook_applies_live_claude_deselect_marker() -> None:
    """Live tests get a real ``live_claude`` marker; nothing else does (HATS-583).

    Unlike the ``xdist_group`` (loadgroup-only), this is a normal marker so
    ``-m "not live_claude"`` deselects the live cohort in every run mode.

    Fail-under-revert: drop ``item.add_marker(pytest.mark.live_claude)`` from
    ``pytest_collection_modifyitems`` → the live item carries no ``live_claude``
    marker and the first assertion fails.
    """
    items = [
        _FakeItem("tests/e2e/test_live.py::test_a", live=True),
        _FakeItem("tests/e2e/test_plain.py::test_a"),
        _FakeItem("tests/e2e/test_ph.py::test_a", install_heavy=True),
    ]
    _modifyitems(None, items)

    assert "live_claude" in items[0].markers, "live test must carry the marker"
    assert "live_claude" not in items[1].markers, "plain test must not"
    assert "live_claude" not in items[2].markers, "install_heavy test must not"


def test_hook_keeps_a_files_install_heavy_tests_together() -> None:
    """All install-heavy tests of one file share a group (module-fixture coherence)."""
    items = [
        _FakeItem("tests/e2e/test_same.py::test_a", install_heavy=True),
        _FakeItem("tests/e2e/test_same.py::test_b", install_heavy=True),
        _FakeItem("tests/e2e/test_same.py::test_c", install_heavy=True),
    ]
    _modifyitems(None, items)
    assert len({it.group for it in items}) == 1


def test_live_takes_precedence_over_install_heavy() -> None:
    """A test that is BOTH live and install_heavy pins to live_claude.

    Locks the documented precedence (live_claude > install_heavy > per-file). The
    case is currently vacuous (no live test is install_heavy) and harmless either
    way — live_claude already pins to one worker (effective concurrency 1 < K) —
    but this guards the ordering against a future test that gates on both.
    """
    items = [_FakeItem("tests/e2e/test_x.py::test_a", install_heavy=True, live=True)]
    _modifyitems(None, items)
    assert items[0].group == "live_claude"
