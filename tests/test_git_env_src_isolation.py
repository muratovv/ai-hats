"""HATS-890 — in-process regression: src git helpers ignore an ambient GIT_DIR.

Drives the cwd-scoped git helpers directly with ``GIT_DIR`` / ``GIT_WORK_TREE``
pinned at a DECOY repo; each must still resolve the tmp repo passed via ``cwd``.
RED-under-revert: drop a helper's ``env=_scrubbed_git_env()`` (or
``scrubbed_git_env()``) and it resolves the decoy's ``.git``. This is the runtime
counterpart to the static ``test_git_env_hygiene`` src lint.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.retro.facts import _git as facts_git
from ai_hats.wt.locks import _stale_index_lock_age
from ai_hats.wt.manager import WorktreeManager


def _init_repo(path: Path) -> Path:
    """git-init ``path`` with one empty commit. Runs while conftest's autouse
    ``_isolate_git_env`` keeps GIT_* clean, so setup can't hit the decoy."""
    path.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-b", "master"],
        ["config", "user.email", "t@e"],
        ["config", "user.name", "T"],
        ["commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True)
    return path


@pytest.fixture()
def repos(tmp_path, monkeypatch):
    """A real ``target`` repo + a ``decoy`` repo, with the decoy pinned as the
    ambient GIT_DIR/GIT_WORK_TREE the helpers must ignore."""
    target = _init_repo(tmp_path / "target")
    decoy = _init_repo(tmp_path / "decoy")
    # Set the ambient plumbing AFTER both repos exist (setup stays clean).
    monkeypatch.setenv("GIT_DIR", str((decoy / ".git").resolve()))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy.resolve()))
    monkeypatch.setenv("GIT_INDEX_FILE", str((decoy / ".git" / "index").resolve()))
    return target, decoy


def test_worktree_manager_git_ignores_ambient_git_dir(repos):
    """``WorktreeManager._git`` resolves ``project_dir``, not the ambient GIT_DIR."""
    target, decoy = repos
    mgr = WorktreeManager(target, branch_name="task/probe")
    resolved = Path(mgr._git("rev-parse", "--absolute-git-dir").stdout.strip()).resolve()
    assert resolved == (target / ".git").resolve(), (
        f"🐛 HATS-890 REGRESSION: WorktreeManager._git leaked the ambient GIT_DIR "
        f"→ resolved {resolved}, not the target repo {(target / '.git').resolve()}."
    )


def test_retro_facts_git_ignores_ambient_git_dir(repos):
    """``retro.facts._git`` resolves the passed ``project_dir``, not the decoy."""
    target, decoy = repos
    out = facts_git(target, ["rev-parse", "--absolute-git-dir"]).strip()
    assert Path(out).resolve() == (target / ".git").resolve(), (
        f"retro.facts._git leaked the ambient GIT_DIR → {out}"
    )


def test_locks_index_lock_probe_ignores_ambient_git_dir(repos):
    """``wt.locks._stale_index_lock_age`` finds the TARGET repo's index.lock via
    ``rev-parse --git-common-dir`` scoped to ``cwd`` — not the decoy's (absent)."""
    target, decoy = repos
    (target / ".git" / "index.lock").write_text("")  # simulate a stale lock
    result = _stale_index_lock_age(target, threshold_s=0.0)
    assert result is not None, (
        "index-lock probe returned None — it resolved the decoy's --git-common-dir "
        "(no index.lock there) instead of the target repo's."
    )
    _age, lock_path = result
    assert lock_path.resolve() == (target / ".git" / "index.lock").resolve()
