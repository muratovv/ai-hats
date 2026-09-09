"""e2e (HATS-766)

flow:   a developer running piped installer script via stdin without a local git clone
cmds:
    curl -sSL https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh
    | bash
expect: installer fetches launcher over network and writes launcher script defaulting to
        git+https source
why: without piped stdin installer support, users without local repo clones cannot
     install
        the host launcher binary"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_LAUNCHER_DEST

pytestmark = pytest.mark.install

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
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"piped install failed:\n{combined}"
    assert dest.is_file() and os.access(dest, os.X_OK), "launcher not installed/executable"
    assert "fetching" in combined, f"piped curl branch not taken:\n{combined}"
    # R1: the installed launcher defaults to the anonymous git+https source.
    installed = dest.read_text()
    assert (
        'REPO_URL="${AI_HATS_REPO_URL:-git+https://github.com/muratovv/ai-hats.git}"' in installed
    ), "installed launcher does not carry the git+https default"
