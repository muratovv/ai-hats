"""Drift lock between the launcher's stamped contract and the package constant (HATS-1617).

The number is declared twice — as a bash literal the launcher can read without a
working venv, and as a Python constant the ``self update`` advisory compares
against. Two declarations drift silently, and a drifted detector lies in BOTH
directions: mute on a real skew, or crying skew at a current launcher.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats.cli.maintenance import _launcher_contract_skew, read_launcher_contract
from ai_hats.constants import ENV_LAUNCHER_DEST, LAUNCHER_CONTRACT

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "scripts" / "ai-hats-launcher"


def test_launcher_stamp_matches_package_constant() -> None:
    assert read_launcher_contract(LAUNCHER) == LAUNCHER_CONTRACT


def test_contract_starts_above_the_unstamped_sentinel() -> None:
    """0 is reserved for "carries no stamp", so a live contract must exceed it."""
    assert LAUNCHER_CONTRACT >= 1


def test_unstamped_launcher_reads_as_pre_contract(tmp_path: Path) -> None:
    launcher = tmp_path / "ai-hats"
    launcher.write_text('#!/usr/bin/env bash\nexec python -m ai_hats "$@"\n')
    assert read_launcher_contract(launcher) == 0


def test_unreadable_launcher_is_indeterminate(tmp_path: Path) -> None:
    """Absent ≠ unstamped: an unreadable file must not be reported as behind."""
    assert read_launcher_contract(tmp_path / "nowhere") is None


def test_stamp_is_read_only_at_line_start(tmp_path: Path) -> None:
    """A mention inside a comment or string is not the declaration."""
    launcher = tmp_path / "ai-hats"
    launcher.write_text("#!/usr/bin/env bash\n# see LAUNCHER_CONTRACT=99 elsewhere\n")
    assert read_launcher_contract(launcher) == 0


# --------------------------------------------------------------------- #
# The `self update` side: skew against the launcher actually installed.
# Covers the success path, where the launcher runs fine but resolves stale.
# --------------------------------------------------------------------- #


def _installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    launcher = tmp_path / "ai-hats"
    launcher.write_text(body)
    monkeypatch.setenv(ENV_LAUNCHER_DEST, str(launcher))


def test_skew_when_installed_launcher_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _installed(tmp_path, monkeypatch, "#!/usr/bin/env bash\n")  # unstamped ⇒ 0
    assert _launcher_contract_skew() == (0, LAUNCHER_CONTRACT)


def test_no_skew_when_launcher_is_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _installed(tmp_path, monkeypatch, f"LAUNCHER_CONTRACT={LAUNCHER_CONTRACT}\n")
    assert _launcher_contract_skew() is None


def test_no_skew_when_launcher_is_ahead(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One host launcher serves N projects — newer than this one is not skew."""
    _installed(tmp_path, monkeypatch, f"LAUNCHER_CONTRACT={LAUNCHER_CONTRACT + 5}\n")
    assert _launcher_contract_skew() is None


def test_no_skew_when_launcher_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Indeterminate must not be reported as behind."""
    monkeypatch.setenv(ENV_LAUNCHER_DEST, str(tmp_path / "nowhere"))
    assert _launcher_contract_skew() is None
