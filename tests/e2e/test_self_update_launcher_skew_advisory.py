"""e2e (HATS-647, HATS-655, HATS-1617)

flow:   a developer running self update when the host launcher binary is older than
        the installed framework
cmds:
    ai-hats self update
expect: self update names the contract skew and prints the launcher refresh command
why:    the launcher is a copy that never self-updates. This is the SUCCESS-path
        contour — the update completes, so the failure-path check inside the
        launcher (tests/e2e/test_launcher_contract_skew.py) never runs here
"""

from __future__ import annotations
from _helpers.git import git

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.workspace import build_workspace_member_wheels
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# A stale launcher: resolves ONLY the legacy .venv (no versions/current block,
# no pin-at-spawn), mirroring a pre-HATS-647 install. Heals .venv on self update
# so the first migration update can bootstrap. HATS-790: there is no bin/ai-hats
# console script, so usability is probed via `python -c "import ai_hats"` and the
# package is dispatched via `python -m ai_hats` (this stub's "stale" property is
# that it ignores versions/current — not its dispatch mechanism).
STALE_LAUNCHER = """#!/usr/bin/env bash
set -euo pipefail
PROJECT="$(pwd)"
VENV="$PROJECT/.agent/ai-hats/.venv"
REPO_URL="${AI_HATS_REPO_URL:?}"
if [[ "$REPO_URL" == *"://"* ]]; then PIP_TARGET="ai-hats @ $REPO_URL"; else PIP_TARGET="$REPO_URL"; fi
export AI_HATS_VENV="$VENV"
if [[ "${1:-}" == "self" && "${2:-}" == "update" ]]; then
  if ! { [[ -x "$VENV/bin/python" ]] && "$VENV/bin/python" -c "import ai_hats" 2>/dev/null; }; then
    rm -rf "$VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet "$PIP_TARGET"
  fi
fi
exec "$VENV/bin/python" -m ai_hats "$@"
"""


def _run(cmd, *, cwd, env, timeout):
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.integration
def test_e2e_stale_launcher_contract_advisory(tmp_path: Path) -> None:
    src_repo = tmp_path / "src-repo"
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )
    git(src_repo, "config", "user.email", "e2e@test")
    git(src_repo, "config", "user.name", "E2E")
    git(src_repo, "checkout", "-B", "e2e-main")  # HATS-764: align ls-remote HEAD
    sha_a = _head_sha(src_repo)

    # Install the STALE launcher shim.
    launcher.write_text(STALE_LAUNCHER)
    launcher.chmod(0o755)
    launcher_bytes_before = launcher.read_bytes()

    env = os.environ.copy()
    env[ENV_REPO_URL] = str(src_repo)
    # HATS-1617: name the shim as THE installed launcher — the skew check reads
    # this file's stamp, so leaving it to `which` would test the host's launcher.
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)
    # HATS-898: ai-hats requires unpublished ai-hats-core/ai-hats-wt — build the
    # member wheels from the clone; the shim's pip + self-update's uv resolve them.
    wsdir = build_workspace_member_wheels(src_repo, tmp_path / "workspace-wheels", env)
    env["UV_FIND_LINKS"] = str(wsdir)
    env["PIP_FIND_LINKS"] = str(wsdir)

    versions = project / ".agent" / "ai-hats" / "versions"

    # HATS-1617: the shim carries no LAUNCHER_CONTRACT stamp, so it reads as
    # contract 0 and the advisory fires on the FIRST update — the old check had to
    # wait for a versioned install to exist before any symptom appeared.
    r1 = _run([str(launcher), "self", "update"], cwd=project, env=env, timeout=300)
    assert r1.returncode == 0, f"update failed:\n{r1.stdout}\n{r1.stderr}"
    assert (versions / "current").read_text().strip() == sha_a
    out1 = r1.stdout + r1.stderr
    assert "host launcher is behind this install" in out1, (
        f"contract-skew advisory did not fire.\nstdout:\n{r1.stdout}\nstderr:\n{r1.stderr}"
    )
    assert "install-launcher.sh" in out1  # actionable fix surfaced

    # --- Non-mutation invariant: the launcher file was never touched. ---
    assert launcher.read_bytes() == launcher_bytes_before, (
        "the advisory must NEVER write the host launcher"
    )


def _git(args, cwd):
    return git(cwd, *args)
