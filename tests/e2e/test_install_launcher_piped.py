"""E2E: the PIPED install-launcher path (curl | bash) installs keyless (HATS-766).

The public one-liner is ``curl -sSL .../install-launcher.sh | bash``. When piped
(no local clone on disk) the installer fetches the launcher itself over the
network. HATS-766 made the repo public, so this path:

  - no longer needs the dead private-repo HTML-404 guard (removed), and
  - the installed launcher carries the anonymous ``git+https`` default (R1).

This test drives the piped branch WITHOUT network by pointing
``AI_HATS_LAUNCHER_URL`` at a ``file://`` URL (curl supports it) and feeding the
installer to ``bash`` over stdin so ``BASH_SOURCE`` is unset → the local-clone
``SRC`` detection misses → the curl branch runs.

Fail-under-revert: revert the launcher ``REPO_URL`` default to ``git+ssh`` and
the installed-launcher assertion below fails. Per ``dev_rule_e2e_gate``: real
``bash`` + real ``curl`` + real installed launcher file.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_LAUNCHER_DEST

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LAUNCHER_SRC = REPO_ROOT / "scripts" / "ai-hats-launcher"
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


@pytest.mark.integration
def test_e2e_install_launcher_piped_from_file_url(tmp_path: Path) -> None:
    """Piped (stdin) install via a file:// launcher URL installs an https-default launcher."""
    dest = tmp_path / "bin" / "ai-hats"
    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(dest)
    env["AI_HATS_LAUNCHER_URL"] = f"file://{LAUNCHER_SRC}"

    # Feed the installer to bash over stdin → no BASH_SOURCE → SRC stays empty →
    # the piped curl branch (the code path changed by R2) runs.
    result = subprocess.run(
        ["bash"],
        input=INSTALL_LAUNCHER.read_text(),
        cwd=str(tmp_path), env=env,
        capture_output=True, text=True, timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"piped install failed:\n{combined}"
    assert dest.is_file() and os.access(dest, os.X_OK), "launcher not installed/executable"
    assert "fetching" in combined, f"piped curl branch not taken:\n{combined}"
    # R1: the installed launcher defaults to the anonymous git+https source.
    installed = dest.read_text()
    assert 'REPO_URL="${AI_HATS_REPO_URL:-git+https://github.com/muratovv/ai-hats.git}"' in installed, (
        "installed launcher does not carry the git+https default"
    )


def test_install_launcher_html_guard_removed() -> None:
    """R2: the dead private-repo HTML-404 guard + stale comments are gone (structural lock)."""
    text = INSTALL_LAUNCHER.read_text()
    lowered = text.lower()
    assert "<!doctype html" not in lowered, "HTML-404 guard pattern still present"
    assert "received html instead of a script" not in lowered, "HTML-guard error still present"
    assert "repo is private" not in lowered, "stale private-repo comment still present"
    assert "repo is currently private" not in lowered
