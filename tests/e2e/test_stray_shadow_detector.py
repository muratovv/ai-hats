"""e2e (HATS-791)

flow: a developer running bootstrap repair script when stray ai-hats binaries exist on
      PATH
cmds:
    bash scripts/bootstrap.sh --repair
expect: repair script detects stray ai-hats executables on PATH and emits remediation
        warnings
        without deleting files
why: without stray shadow detection, outdated global binaries on PATH shadow venv
     launchers causing
        unexplainable errors"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_LAUNCHER_DEST

pytestmark = [pytest.mark.integration, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stub_launcher(path: Path, log: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'#!/usr/bin/env bash\necho "REPO=${{AI_HATS_REPO_URL:-}}|ARGS=$*" >> "{log}"\nexit 0\n'
    )
    _make_executable(path)


def test_repair_warns_on_stray_launcher(tmp_path: Path) -> None:
    """A stray ai-hats on PATH outside LAUNCHER_DEST is named (and not deleted)."""
    # Fake scripts dir with the real bootstrap.sh + stub installer/launcher.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    bootstrap = scripts / "bootstrap.sh"
    shutil.copy(BOOTSTRAP, bootstrap)
    log = tmp_path / "calls.log"

    # Stub launcher the installer will deposit at LAUNCHER_DEST.
    stub = scripts / "ai-hats-launcher"
    _stub_launcher(stub, log)
    installer = scripts / "install-launcher.sh"
    installer.write_text(
        "#!/usr/bin/env bash\nset -e\n"
        'mkdir -p "$(dirname "$AI_HATS_LAUNCHER_DEST")"\n'
        f'cp "{stub}" "$AI_HATS_LAUNCHER_DEST"\n'
        'chmod +x "$AI_HATS_LAUNCHER_DEST"\nexit 0\n'
    )
    _make_executable(installer)

    # Sanctioned launcher dest (NOT on PATH below — its dir is excluded so only
    # the stray is found via PATH scanning).
    launcher_dest = tmp_path / "sanctioned" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    # A STRAY ai-hats on PATH, in a foreign dir != sanctioned dest.
    stray_dir = tmp_path / "appvenv" / "bin"
    stray = stray_dir / "ai-hats"
    _stub_launcher(stray, tmp_path / "stray.log")

    project = tmp_path / "project"
    project.mkdir()

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    # PATH carries the stray dir + system bins (bash/uv resolution); deliberately
    # NOT the sanctioned dir, so the detector sees exactly one stray.
    env["PATH"] = os.pathsep.join([str(stray_dir), env.get("PATH", "/usr/bin:/bin")])

    res = subprocess.run(
        ["bash", str(bootstrap), "--repair"],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    combined = res.stdout + res.stderr
    assert res.returncode == 0, f"bootstrap --repair failed:\n{combined}"

    # The stray is flagged by path, under the shadow advisory.
    assert "shadow" in combined, f"stray-shadow advisory missing:\n{combined}"
    assert str(stray) in combined, f"stray path not named:\n{combined}"

    # NEVER deletes — destructive-actions rule.
    assert stray.is_file(), "detector must NOT delete the stray (warn-only)"
