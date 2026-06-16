"""E2E: the bash launcher heal is channel-aware — channel:local heals EDITABLE (HATS-766).

Value under test (caveat b): when a ``channel: local`` project's managed
``.venv`` is missing/broken, the launcher's ``heal_if_needed()`` must rebuild it
as an EDITABLE install (``uv pip install -e <harness.path>``), mirroring
``maintenance._run_editable_update`` — NOT a non-editable ``PIP_TARGET`` snapshot
that clobbers the dev working-tree install.

Why ``self init`` and not ``self update`` (review P1-4): the launcher heals on
BOTH, but ``self update`` on channel:local then re-runs the PYTHON editable
reinstall (``_run_editable_update``), which would MASK a non-editable launcher
heal — the venv ends up editable regardless. ``self init -r <role> -p <provider>``
with non-TTY stdin takes the no-wizard path (``use_wizard=False``) so the python
side runs NO pip install: the launcher heal is the ONLY installer, and its
editability is observable end-to-end.

Discriminator: ``AI_HATS_REPO_URL`` and ``harness.path`` both point at the same
local checkout, so the revert (non-editable ``PIP_TARGET`` rebuild) installs a
NON-editable copy from ``AI_HATS_REPO_URL`` → ``dir_info.editable`` is false →
the assertion fails. The fix installs ``-e <harness.path>`` → editable true.

Setup contract (real subprocess + real uv + real launcher), per
``dev_rule_e2e_gate``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.pip_heavy  # real uv install at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY → self init takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_launcher_heal_channel_local_is_editable(tmp_path: Path) -> None:
    """A missing venv on a channel:local project heals editable via the launcher alone."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # channel:local installs `-e <path>`; point both the channel path AND the
    # launcher's PIP_TARGET at the same real checkout so the revert path
    # (non-editable PIP_TARGET) is deterministic + offline yet still NON-editable.
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    (project / "ai-hats.yaml").write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)  # PIP_TARGET source (revert path)
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    # No prior `self update` — `self init` is the FIRST command, so its launcher
    # heal is the only installer. -r/-p + non-TTY stdin ⇒ no-wizard ⇒ no python
    # reinstall to mask the heal.
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=300,
    )

    ai_hats_dir = project / ".agent" / "ai-hats"
    venv = ai_hats_dir / ".venv"
    assert (venv / "bin" / "ai-hats").is_file(), "healed .venv ai-hats missing"
    # In-place editable heal creates no blue-green versions/ dir.
    assert not (ai_hats_dir / "versions").exists(), (
        "channel:local heal must be in place — no versions/ dir"
    )
    # The launcher heal alone produced an editable install (PEP 610).
    dist_info = list((venv / "lib").glob("python*/site-packages/ai_hats-*.dist-info"))
    assert dist_info, "ai-hats dist-info not found in healed .venv"
    direct_url = json.loads((dist_info[0] / "direct_url.json").read_text())
    assert direct_url.get("dir_info", {}).get("editable") is True, (
        f"launcher heal of channel:local is not editable: {direct_url}"
    )
