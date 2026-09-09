"""e2e (HATS-1556)

flow:   a developer executing printed rescue command when automatic bootstrap heal fails
cmds:
    python -m ai_hats --version
expect: gate prints manual repair command that successfully restores workspace
        dependencies
why:    without accurate rescue commands, manual repair instructions fail to restore
        editable workspace packages
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RESCUE_RE = re.compile(r"^\s*manual command(?: if this fails)?: (uv pip install .+)$", re.M)


@pytest.mark.integration
def test_printed_rescue_command_repairs_the_venv(tmp_path: Path) -> None:
    """Whatever bootstrap tells the user to run must leave a working install."""
    from _helpers.editable_venv import (
        build_editable_venv,
        imports,
        make_metadata_predate_workspace_split,
        module_file,
    )
    from _helpers.venv import network_available, venv_unavailable

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build an editable venv")
    uv = shutil.which("uv")

    try:
        venv_python, checkout = build_editable_venv(tmp_path, REPO_ROOT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        venv_unavailable(f"editable venv build failed/timed out: {exc}")

    make_metadata_predate_workspace_split(venv_python)

    # PATH without uv: the heal cannot run, so the gate prints the manual command
    # and exits 1 — the branch a user actually reads.
    no_uv = tmp_path / "no-uv-bin"
    no_uv.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PATH"] = str(no_uv)
    result = subprocess.run(
        [str(venv_python), "-m", "ai_hats", "--version"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )

    assert result.returncode == 1, (
        f"expected a loud exit(1) with no uv available, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    match = RESCUE_RE.search(result.stderr)
    assert match, f"no rescue command on stderr:\n{result.stderr}"

    rescue = match.group(1).replace("uv pip install", f"{uv} pip install", 1)
    repair = subprocess.run(  # noqa: S602 — running the printed command AS a user would is the test
        rescue,
        shell=True,
        cwd=str(tmp_path),  # a consumer project, NOT the ai-hats workspace: from
        # inside it uv resolves first-party names via [tool.uv.sources] and even
        # the broken by-name rescue would appear to work.
        capture_output=True,
        text=True,
        timeout=600,
    )

    assert repair.returncode == 0, (
        f"the printed rescue command failed:\n{rescue}\n"
        f"stdout:\n{repair.stdout}\nstderr:\n{repair.stderr}"
    )
    assert imports(venv_python, "ai_hats_wt"), (
        f"the printed rescue command ran but repaired nothing:\n{rescue}"
    )
    # Repaired, not replaced: the workspace members are published, so a by-name
    # rescue "succeeds" by pulling PyPI wheels over the developer's checkout —
    # observed here as ai-hats-observe 0.4.0 displacing the workspace 0.5.0.
    healed_from = module_file(venv_python, "ai_hats_wt")
    assert healed_from.startswith(str(checkout)), (
        f"rescue restored ai_hats_wt from {healed_from}, not from the editable "
        f"checkout at {checkout} — the install was replaced, not repaired:\n{rescue}"
    )
