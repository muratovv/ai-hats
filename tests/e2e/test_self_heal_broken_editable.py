"""E2E: the launcher auto-heals a stale surface-plugin editable before exec (HATS-966).

Reproduces the real incident end-to-end with a real venv + real uv + the real
bash launcher: the ``cline`` surface plugin is editable-installed, then its
editable ``.pth`` is rewritten to a deleted path (the dangling-worktree state) —
its canonical ``packages/surfaces/cline`` is left intact. A bare
``ai-hats list providers`` (a non-``self`` command → the launcher fall-through)
must: probe → flag the stale plugin on the side channel → re-point it via
``self heal-editables`` → then ``exec`` the user's command, which now lists
``cline`` again.

Fail-under-revert: remove the launcher's ``PROBE_BROKEN_PLUGINS`` heal branch
(``scripts/ai-hats-launcher``) → the stale ``.pth`` is never re-pointed → ``cline``
stays unimportable and absent from ``list providers`` → this test fails. Reverting
the ``self_heal`` module or the ``self heal-editables`` wiring fails it too (the
launcher's heal call then errors / no-ops).

Setup contract (real subprocess + real uv + real launcher), per
``dev_rule_e2e_gate``. install_heavy: capped concurrency via conftest.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real uv install at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _imports(vpy: Path, module: str, env) -> bool:
    return subprocess.run(
        [str(vpy), "-c", f"import {module}"], env=env,
        capture_output=True, text=True,
    ).returncode == 0


@pytest.mark.integration
def test_e2e_launcher_auto_heals_stale_surface_plugin(tmp_path: Path) -> None:
    """A dangling ``cline`` editable is re-pointed by the launcher before exec."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    # self init builds the channel:local venv (ai-hats editable from src_repo).
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=300,
    )

    venv = project / ".agent" / "ai-hats" / ".venv"
    vpy = venv / "bin" / "python"
    assert vpy.is_file(), "healed venv python missing"

    # Install the cline surface plugin editable into the venv (uv resolves the
    # workspace from the package path, not cwd). This is the provider we break.
    _run(
        ["uv", "pip", "install", "--no-deps", "--python", str(vpy),
         "-e", str(src_repo / "packages" / "surfaces" / "cline")],
        cwd=tmp_path, env=env, timeout=120,
    )
    assert _imports(vpy, "ai_hats_cline", env), "cline should import after install"

    # Break it: rewrite the editable .pth to a deleted path — the dangling state —
    # while leaving the canonical src_repo/packages/surfaces/cline intact.
    pths = list((venv / "lib").glob("python*/site-packages/*ai_hats_cline*.pth"))
    assert pths, "cline editable .pth not found"
    pths[0].write_text("/tmp/gone-hats966-e2e/packages/surfaces/cline/src\n")
    assert not _imports(vpy, "ai_hats_cline", env), (
        "cline should be broken after the .pth rewrite"
    )

    # Drive the REAL launcher with a non-`self` command. The fall-through probe
    # flags cline; the heal branch re-points it BEFORE exec; the fresh
    # `list providers` then lists it.
    result = _run([str(launcher_dest), "list", "providers"], cwd=project, env=env, timeout=180)

    assert _imports(vpy, "ai_hats_cline", env), (
        "launcher did not re-point the stale cline editable before exec\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "cline" in result.stdout, (
        "healed cline not shown by `list providers`\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
