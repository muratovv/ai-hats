"""E2E: ``ai-hats self update --check`` triages the install layers (HATS-595).

Value under test: recovering a partially-destroyed ``.agent/`` used to require
source-diving to learn which pieces are DATA (hand-authored, snapshot-only),
MANAGED (rebuilt by ``self init``), or RUNTIME (rebuilt by ``self update``).
``--check`` answers that read-only, and its exit code makes the verdict
machine-detectable: 0 when healthy or warn-only, 1 when a layer is broken.

Fail-under-revert: drop the ``sys.exit(1 if ... BROKEN else 0)`` branch from
``update()`` in ``cli/maintenance.py`` and the broken-layer run exits 0 — the
``expect_exit=1`` assertion below fails.

Setup contract (real subprocess + real uv + real launcher + real ``ai-hats``
binary), per ``dev_rule_e2e_gate``. Uses the ``local`` channel so the run is
offline and network-free.
"""

from __future__ import annotations

import os
import shutil
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
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_self_update_check_triages_layers(tmp_path: Path) -> None:
    """Healthy install exits 0; a missing MANAGED layer exits 1 naming self init."""
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
    env[ENV_REPO_URL] = str(src_repo)  # launcher bootstrap source
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "init", "-r", "assistant"], cwd=project, env=env, timeout=300)

    # ----- healthy install: diagnose-only, exit 0 -----
    healthy = _run(
        [str(launcher_dest), "self", "update", "--check"], cwd=project, env=env, timeout=120
    )
    assert "Layer triage" in healthy.stdout
    assert "MANAGED" in healthy.stdout

    # ----- HATS-1163: a declared-but-missing wt_out script is a broken layer -----
    # The state that blocked the HATS-595 merge: the manifest still names the script
    # the teardown resolves by name, but the file is gone. Seeded explicitly rather
    # than relying on the composed role to ship a wt_out hook — a conditional here
    # would pass vacuously on any role that composes none.
    wt_hooks = project / ".agent" / "ai-hats" / "library" / "wt-hooks"
    wt_hooks.mkdir(parents=True, exist_ok=True)
    hook = wt_hooks / "hunk-review-comments-drain-review.sh"
    (wt_hooks / ".manifest").write_text(
        f"# ai-hats managed — do not edit\n{hook.name}\n", encoding="utf-8"
    )
    hook.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    _run([str(launcher_dest), "self", "update", "--check"], cwd=project, env=env, timeout=120)

    hook.unlink()
    missing_hook = _run(
        [str(launcher_dest), "self", "update", "--check"],
        cwd=project,
        env=env,
        timeout=120,
        expect_exit=1,
    )
    assert hook.name in missing_hook.stdout, (
        "--check must name the wt_out script the manifest declares but disk lacks"
    )
    shutil.rmtree(wt_hooks)

    # ----- destroy a MANAGED layer: exit 1 + the exact remediation -----
    library = project / ".agent" / "ai-hats" / "library"
    assert library.is_dir(), f"init did not materialize {library}"
    shutil.rmtree(library)

    broken = _run(
        [str(launcher_dest), "self", "update", "--check"],
        cwd=project,
        env=env,
        timeout=120,
        expect_exit=1,
    )
    assert "BROKEN" in broken.stdout
    assert "ai-hats self init" in broken.stdout, (
        "--check must name the remediation for a broken MANAGED layer (HATS-595)"
    )
    assert library.parent.joinpath("library").exists() is False, (
        "--check must be read-only — it rebuilt the layer it was asked to diagnose"
    )
