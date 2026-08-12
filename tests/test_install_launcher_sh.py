"""Structural locks on scripts/install-launcher.sh (HATS-766).

The script is read as text, not run — the piped-install behaviour it guards is
covered by ``tests/e2e/test_install_launcher_piped.py``, which spawns a real
bash.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def test_install_launcher_html_guard_removed() -> None:
    """R2: the dead private-repo HTML-404 guard + stale comments are gone (structural lock)."""
    text = INSTALL_LAUNCHER.read_text()
    lowered = text.lower()
    assert "<!doctype html" not in lowered, "HTML-404 guard pattern still present"
    assert "received html instead of a script" not in lowered, "HTML-guard error still present"
    assert "repo is private" not in lowered, "stale private-repo comment still present"
    assert "repo is currently private" not in lowered
