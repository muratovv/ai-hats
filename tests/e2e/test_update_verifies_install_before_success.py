"""e2e (HATS-1239)

flow: a developer running self update when python installation source carries import
      errors
cmds:
    ai-hats self update
expect: post-install verification fails, self update exits non-zero, and success message
        is never
        printed
why: without post-install verification, corrupted updates report success while leaving
     the tool in
        a broken state"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

# own launcher venv + real uv install
pytestmark = [pytest.mark.integration, pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MISSING_SYMBOL = 'PROVIDER_CLAUDE = "claude"'


def _broken_install_source(dst: Path) -> Path:
    """Clone the repo, overlay the live ``src/``, then remove a symbol siblings import."""
    src = dst / "broken-src"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(REPO_ROOT), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Overlay the working tree so a dirty checkout is tested, not the last commit.
    shutil.copytree(REPO_ROOT / "src", src / "src", dirs_exist_ok=True)

    constants = src / "src" / "ai_hats" / "constants.py"
    text = constants.read_text()
    if _MISSING_SYMBOL not in text:
        pytest.skip(f"{_MISSING_SYMBOL!r} no longer in constants.py — pick another symbol")
    constants.write_text(text.replace(_MISSING_SYMBOL, "", 1))
    return src


def test_update_does_not_report_success_for_a_broken_install(tmp_path: Path, repo_root: Path):
    """uv exit 0 + unusable tree → red diagnosis and a non-zero exit, never ``✓``."""
    from _helpers.project import pin_edge_channel
    from _helpers.venv import build_launcher_venv

    try:
        launcher, venv = build_launcher_venv(tmp_path / "host", repo_root)
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"launcher venv unavailable: {exc}")

    project = tmp_path / "project"
    project.mkdir()
    pin_edge_channel(project)

    env = {
        **os.environ,
        "AI_HATS_ALLOW_SELF_UPDATE_IN_TEST": "1",
        ENV_REPO_URL: str(_broken_install_source(tmp_path)),
        ENV_AI_HATS_VENV: str(venv),
    }

    try:
        proc = subprocess.run(
            [str(launcher), "self", "update"],
            cwd=str(project),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("update hung instead of failing the verify")

    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"update exited 0 for a broken install:\n{out}"
    assert "ai-hats updated" not in out, f"success line printed for a broken install:\n{out}"
    assert "Post-install verify failed" in out, (
        f"broken install failure message was not surfaced:\n{out}"
    )
    assert _MISSING_SYMBOL.split(" =")[0] in out, f"verify did not name the breakage:\n{out}"
