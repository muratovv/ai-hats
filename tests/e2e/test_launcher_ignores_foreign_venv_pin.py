"""E2E: launcher ignores an AI_HATS_VENV pinned to a foreign project (HATS-944).

HATS-897 guards the paired ``AI_HATS_DIR`` leak in Python, but venv selection
happens in the bash launcher *before* ``python -m ai_hats`` is exec'd, so the
``AI_HATS_VENV`` half needs its own guard there. When an agent session pinned to
project A (``AI_HATS_VENV`` + ``AI_HATS_PROJECT_DIR``) runs ``ai-hats`` from
project B, the launcher must re-resolve B's venv, not honor A's.

Fail-under-revert: without the guard the launcher execs the foreign (missing) A
venv → "venv missing" exit 1 instead of running B's healthy venv (exit 0). Real
subprocess + real uv + real launcher per ``dev_rule_e2e_gate``.
"""
# comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real uv installs at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_foreign_venv_pin_ignored(tmp_path: Path) -> None:
    """A mismatched AI_HATS_VENV/AI_HATS_PROJECT_DIR pair is ignored (+warn); a
    bare override and a matching pin keep working."""
    src_repo = tmp_path / "src-repo"
    launcher = tmp_path / "bin" / "ai-hats"
    project_b = tmp_path / "project-b"
    project_a = tmp_path / "project-a"  # the "other" project the pin points at
    launcher.parent.mkdir(parents=True)
    project_b.mkdir()
    project_a.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    (project_b / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(src_repo)
    # The test runner itself may run inside a pinned ai-hats session — strip the
    # inherited pair so the base install/init resolves B cleanly.
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop(AI_HATS_PROJECT_DIR_ENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project_b, env=env, timeout=300,
    )
    b_venv = project_b / ".agent" / "ai-hats" / ".venv"
    assert (b_venv / "bin" / "python").exists(), "premise broken: B venv not built by self init"

    # The foreign pin: a venv path under project A that was never created.
    a_venv = project_a / ".agent" / "ai-hats" / ".venv"

    # --- Case (a): mismatched pair → ignored + warn, B's venv used (exit 0). ---
    foreign = dict(env)
    foreign[ENV_AI_HATS_VENV] = str(a_venv)
    foreign[AI_HATS_PROJECT_DIR_ENV] = str(project_a)
    res_a = _run([str(launcher), "--version"], cwd=project_b, env=foreign, timeout=120)
    assert "foreign to" in res_a.stderr and "ignoring the leaked session pin" in res_a.stderr, (
        f"expected foreign-pin warning, got stderr:\n{res_a.stderr}"
    )
    assert "venv missing" not in res_a.stderr, (
        f"launcher honored the foreign venv instead of re-resolving B:\n{res_a.stderr}"
    )
    assert not a_venv.exists(), "guard must not create/heal the foreign A venv"

    # --- Case (b): bare override (no pair) keeps env-wins semantics (R3). ---
    # A missing bare override must still be honored → 'venv missing at <it>',
    # NOT silently re-resolved to B.
    bare = dict(env)
    bare[ENV_AI_HATS_VENV] = str(a_venv)
    bare.pop(AI_HATS_PROJECT_DIR_ENV, None)
    res_b = _run(
        [str(launcher), "--version"], cwd=project_b, env=bare, timeout=120, expect_exit=1
    )
    assert "venv missing" in res_b.stderr and str(a_venv) in res_b.stderr, (
        f"bare override must be honored (not re-resolved), got:\n{res_b.stderr}"
    )
    assert "foreign to" not in res_b.stderr, (
        f"guard must not fire without an AI_HATS_PROJECT_DIR pair:\n{res_b.stderr}"
    )

    # --- Case (c): matching pin is honored, no spurious warning (don't over-fire). ---
    match = dict(env)
    match[ENV_AI_HATS_VENV] = str(b_venv)
    match[AI_HATS_PROJECT_DIR_ENV] = str(project_b)
    res_c = _run([str(launcher), "--version"], cwd=project_b, env=match, timeout=120)
    assert "foreign to" not in res_c.stderr, (
        f"guard fired on a same-project pin:\n{res_c.stderr}"
    )
