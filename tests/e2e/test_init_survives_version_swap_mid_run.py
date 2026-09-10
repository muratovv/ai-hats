"""e2e (HATS-1115, HATS-1126)

flow:   a user runs interactive setup when an embedded update replaces the running
        package distribution with a newer version mid-run
cmds:
    ai-hats self init -p claude
expect: process re-executes cleanly into the updated installation and prints successful
        completion without raising module import errors
why:    replacing an executing package mid-run leaves resident modules in sys.modules
        that raise ImportError when importing updated sibling modules
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest
from ai_hats.constants import ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

# own launcher venv + two real uv installs
pytestmark = [pytest.mark.integration, pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROBE = "PROBE_SYMBOL"
_SPLIT_SIGNATURE = f"cannot import name '{_PROBE}'"


def _divergent_source(dst: Path) -> Path:
    """A repo copy whose assembler imports a constant the installed tree lacks."""
    src = dst / "tree-x"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(REPO_ROOT), str(src)],
        check=True,
        capture_output=True,
        text=True,
    )
    # Overlay the working tree so a dirty checkout is tested, not the last commit.
    shutil.copytree(REPO_ROOT / "src", src / "src", dirs_exist_ok=True)

    constants = src / "src" / "ai_hats" / "constants.py"
    constants.write_text(constants.read_text() + f'\n{_PROBE} = "x"\n')

    assembler = src / "src" / "ai_hats" / "assembler.py"
    text = assembler.read_text()
    anchor = "from .constants import ("
    assert anchor in text, "assembler.py no longer imports from .constants"
    assembler.write_text(text.replace(anchor, f"{anchor}\n    {_PROBE},", 1))
    return src


def _stub_ai_hats_on_path(dst: Path) -> Path:
    """A no-op `ai-hats` so init's wizard hand-off exits instead of spawning a session."""
    bin_dir = dst / "stub-bin"
    bin_dir.mkdir()
    stub = bin_dir / "ai-hats"
    stub.write_text("#!/usr/bin/env bash\nexit 0\n")
    stub.chmod(0o755)
    return bin_dir


def test_init_completes_when_the_update_swaps_the_running_tree(tmp_path: Path, repo_root: Path):
    """The embedded update replaces this interpreter's own package — init must not split."""
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
        "PATH": f"{_stub_ai_hats_on_path(tmp_path)}{os.pathsep}{os.environ['PATH']}",
        ENV_REPO_URL: str(_divergent_source(tmp_path)),
        ENV_AI_HATS_VENV: str(venv),
    }

    master, slave = pty.openpty()  # the wizard path that runs the update needs a TTY
    try:
        proc = subprocess.run(
            [str(launcher), "self", "init", "-p", "claude"],
            cwd=str(project),
            env=env,
            stdin=slave,
            capture_output=True,
            text=True,
            timeout=420,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("init hung after swapping its own tree")
    finally:
        os.close(slave)
        os.close(master)

    out = proc.stdout + proc.stderr
    assert _SPLIT_SIGNATURE not in out, f"split module set after the update:\n{out}"
    assert proc.returncode == 0, out
