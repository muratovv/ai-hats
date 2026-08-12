"""Drift lock between the launcher's stamped contract and the package constant (HATS-1617).

The number is declared twice — as a bash literal the launcher can read without a
working venv, and as a Python constant the ``self update`` advisory compares
against. Two declarations drift silently, and a drifted detector lies in BOTH
directions: mute on a real skew, or crying skew at a current launcher.
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.cli.maintenance import read_launcher_contract
from ai_hats.constants import LAUNCHER_CONTRACT

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


def test_launcher_stamp_matches_package_constant() -> None:
    assert read_launcher_contract(LAUNCHER) == LAUNCHER_CONTRACT


def test_contract_starts_above_the_unstamped_sentinel() -> None:
    """0 is reserved for "carries no stamp", so a live contract must exceed it."""
    assert LAUNCHER_CONTRACT >= 1


def test_unstamped_launcher_reads_as_pre_contract(tmp_path: Path) -> None:
    launcher = tmp_path / "ai-hats"
    launcher.write_text("#!/usr/bin/env bash\nexec python -m ai_hats \"$@\"\n")
    assert read_launcher_contract(launcher) == 0


def test_unreadable_launcher_is_indeterminate(tmp_path: Path) -> None:
    """Absent ≠ unstamped: an unreadable file must not be reported as behind."""
    assert read_launcher_contract(tmp_path / "nowhere") is None


def test_stamp_is_read_only_at_line_start(tmp_path: Path) -> None:
    """A mention inside a comment or string is not the declaration."""
    launcher = tmp_path / "ai-hats"
    launcher.write_text("#!/usr/bin/env bash\n# see LAUNCHER_CONTRACT=99 elsewhere\n")
    assert read_launcher_contract(launcher) == 0
