"""E2E (HATS-890): ``ai-hats config status`` ignores an ambient GIT_DIR.

``dev_rule_e2e_gate`` artifact for ``cli/maintenance.py``. The editable repo-state
line (``_repo_head_for_editable``) shells ``git -C <checkout> rev-parse …``; since
``GIT_DIR`` overrides git's ``-C`` discovery, an ambient ``GIT_DIR`` at a DECOY
repo would surface the decoy's branch — the ``scrubbed_git_env()`` strip prevents
it. Fail-under-revert: drop that strip and the decoy sentinel appears. Like
``test_wt_exec_git_env_isolation``, the shim execs the dev-venv ``python -m
ai_hats``, so GREEN only at the MAIN checkout whose editable src carries the fix.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_SENTINEL_BRANCH = "hats890-decoy-sentinel"


def _init_repo(path: Path, *, branch: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", branch],
        ["config", "user.email", "t@e"],
        ["config", "user.name", "T"],
        ["commit", "--allow-empty", "-m", "decoy"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)
    return path


def _config_status(binary: Path, cwd: Path, env) -> str:
    res = subprocess.run(
        [str(binary), "config", "status"],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0, f"config status failed:\n{res.stdout}\n{res.stderr}"
    return res.stdout + res.stderr


def test_config_status_ignores_ambient_git_dir(tmp_project, tmp_path):
    binary = tmp_project.ai_hats_binary
    decoy = _init_repo(tmp_path / "decoy", branch=_SENTINEL_BRANCH)

    # clean_env drops PYTHONPATH/GIT_* (real installed pkg); re-inject a DECOY
    # GIT_DIR so only the fix under test can keep it out of the inner git calls.
    from _helpers.env import clean_env

    base = clean_env()
    poisoned = {
        **base,
        "GIT_DIR": str((decoy / ".git").resolve()),
        "GIT_WORK_TREE": str(decoy.resolve()),
        "GIT_INDEX_FILE": str((decoy / ".git" / "index").resolve()),
    }

    clean_out = _config_status(binary, tmp_path, base)
    if "clean)" not in clean_out and "dirty)" not in clean_out:
        pytest.skip("ai-hats install not editable — no repo-state line to probe")

    poisoned_out = _config_status(binary, tmp_path, poisoned)
    assert _SENTINEL_BRANCH not in poisoned_out, (
        "🐛 HATS-890 REGRESSION: `config status` leaked the ambient GIT_DIR — the "
        f"editable repo-state resolved the decoy ({_SENTINEL_BRANCH}), not the "
        "ai-hats checkout. `_repo_head_for_editable` lost its GIT_* scrub."
    )
