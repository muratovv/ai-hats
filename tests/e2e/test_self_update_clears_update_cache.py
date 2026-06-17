"""E2E: ``ai-hats self update`` drops the stale update-check cache (HATS-781).

Value under test: the update-check cache is keyed only on ``project_dir`` +
a 24h TTL. Without invalidation, a reinstall within that window leaves the
session-end Update banner reporting the PRE-update installed SHA and a stale
``behind`` count — nagging "update available" the instant the user finished
updating. ``self update`` must unlink
``<project>/.agent/ai-hats/.cache/update-check.json`` on success so the next
session re-probes from scratch.

Fail-under-revert: drop the ``_invalidate_update_cache(project_dir)`` call from
``cli/maintenance.py`` and the seeded cache file survives the ``self update`` —
the final assertion fails.

Setup contract (real subprocess + real uv + real launcher + real ``ai-hats``
binary), per ``dev_rule_e2e_gate``. Uses the ``local`` channel: an offline,
network-free editable reinstall that still exercises the real ``update()``
success exit where the unlink is wired.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

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
def test_e2e_self_update_clears_update_cache(tmp_path: Path) -> None:
    """A successful ``self update`` removes the update-check cache file."""
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)

    # Pin channel: local at the cloned src checkout → offline editable reinstall.
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
    env["AI_HATS_REPO_URL"] = str(src_repo)  # launcher bootstrap source
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    # ----- seed a stale update-check cache before the update -----
    cache_file = project / ".agent" / "ai-hats" / ".cache" / "update-check.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "checked_at": "2026-06-16T08:41:08Z",
                "installed_sha": "86a6bb1a0",
                "latest_sha": "2c8283a337a7a30ea54eb6df1ed9136590b959a8",
                "remote_url": "https://github.com/muratovv/ai-hats.git",
                "behind": 62,
                "ahead": 0,
                "installed_label": None,
                "latest_label": None,
            }
        )
        + "\n"
    )
    assert cache_file.exists()

    # ----- a successful self update must drop it -----
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    assert not cache_file.exists(), (
        "self update did not invalidate the update-check cache — the banner "
        "would keep nagging with the pre-update SHA/delta (HATS-781)"
    )
