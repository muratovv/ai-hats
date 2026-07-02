"""E2E: self update warns when a stale launcher leaves the versioned layout dormant (HATS-655).

Value under test: when the host launcher predates ``versions/current`` resolution
(HATS-647), every ``self update`` builds a ``versions/<sha>/`` the launcher never
uses — the versioned layout is silently dormant. This advisory names what is off
and points at the one-time host-level fix, WITHOUT ever touching the launcher.

Exercised with a deliberately STALE launcher shim (always resolves ``.venv``,
never reads ``versions/current``) + real pip + real ``ai-hats self update`` (per
``dev_rule_e2e_gate``). Deterministic two-update flow, no race.

Flow:
  1. First ``self update`` (migration) — the shim bootstraps ``.venv`` and the
     python self-update builds ``versions/<shaA>`` + flips ``current``. No
     versioned install pre-existed → NO hint (first migration runs from .venv by
     design).
  2. Second ``self update`` (HEAD advanced → shaB) — a versioned install now
     pre-exists, yet the stale shim still runs the updater from ``.venv`` → the
     dormancy hint fires, naming ``versions/<shaB>``.

Invariants asserted:
  - the hint is ABSENT on update 1, PRESENT on update 2;
  - the stale launcher file is BYTE-UNCHANGED across both updates (non-mutation).

Fail-under-revert:
  - removing the hint → update-2 'hint present' assertion fails;
  - an accidental launcher write → the byte-unchanged assertion fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.workspace import build_workspace_member_wheels

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
    )


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _advance(src_repo: Path, marker: str) -> str:
    (src_repo / marker).write_text("hats-655 e2e\n")
    _git(["add", marker], src_repo)
    _git(["commit", "--quiet", "-m", f"test: advance HEAD ({marker})"], src_repo)
    return _head_sha(src_repo)


@pytest.mark.integration
def test_e2e_stale_launcher_dormancy_advisory(tmp_path: Path) -> None:
    src_repo = tmp_path / "src-repo"
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so self update resolves the local source

    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True,
    )
    _git(["config", "user.email", "e2e@test"], src_repo)
    _git(["config", "user.name", "E2E"], src_repo)
    _git(["checkout", "-B", "e2e-main"], src_repo)  # HATS-764: align ls-remote HEAD
    sha_a = _head_sha(src_repo)

    # Install the STALE launcher shim.
    launcher.write_text(STALE_LAUNCHER)
    launcher.chmod(0o755)
    launcher_bytes_before = launcher.read_bytes()

    env = os.environ.copy()
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)
    # HATS-898: ai-hats requires unpublished ai-hats-core/ai-hats-wt — build the
    # member wheels from the clone; the shim's pip + self-update's uv resolve them.
    wsdir = build_workspace_member_wheels(src_repo, tmp_path / "workspace-wheels", env)
    env["UV_FIND_LINKS"] = str(wsdir)
    env["PIP_FIND_LINKS"] = str(wsdir)

    versions = project / ".agent" / "ai-hats" / "versions"

    # --- Update 1: migration. No versioned install pre-existed → no hint. ---
    r1 = _run([str(launcher), "self", "update"], cwd=project, env=env, timeout=300)
    assert r1.returncode == 0, f"update 1 failed:\n{r1.stdout}\n{r1.stderr}"
    assert (versions / "current").read_text().strip() == sha_a
    out1 = r1.stdout + r1.stderr
    assert "host launcher is not using the versioned install" not in out1, (
        "first migration update must NOT warn (running from .venv is expected)"
    )

    # --- Update 2: versioned install pre-exists, stale shim still runs from
    #     .venv → dormancy hint fires. ---
    sha_b = _advance(src_repo, "E2E_655_M1.txt")
    assert sha_b != sha_a
    r2 = _run([str(launcher), "self", "update"], cwd=project, env=env, timeout=300)
    assert r2.returncode == 0, f"update 2 failed:\n{r2.stdout}\n{r2.stderr}"
    assert (versions / "current").read_text().strip() == sha_b
    out2 = r2.stdout + r2.stderr
    assert "host launcher is not using the versioned install" in out2, (
        "dormancy hint must fire once a versioned install is ignored by the "
        f"stale launcher.\nstdout:\n{r2.stdout}\nstderr:\n{r2.stderr}"
    )
    assert "install-launcher.sh" in out2  # actionable fix surfaced

    # --- Non-mutation invariant: the launcher file was never touched. ---
    assert launcher.read_bytes() == launcher_bytes_before, (
        "the advisory must NEVER write the host launcher"
    )
