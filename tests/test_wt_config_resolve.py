"""HATS-942 Step 2 — resolve worktree base_branch / merge_target.

Resolution returns ``Optional[str]``: BOTH unset => ``(None, None)`` (today's
behavior verbatim, NOT a pinned canonical value — caveat Б); a configured value
that is absent in the repo fails loud (R6).
"""

import subprocess
from pathlib import Path

import pytest

from ai_hats.models import ProjectConfig, WorktreeConfig
from ai_hats.wt_config import (
    WorktreeConfigError,
    resolve_base_branch,
    resolve_merge_target,
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo with a committed default branch + an extra `fork-main` branch."""
    p = tmp_path / "repo"
    p.mkdir()
    _git(p, "init")
    _git(p, "config", "user.email", "t@t.com")
    _git(p, "config", "user.name", "T")
    (p / "f").write_text("x")
    _git(p, "add", ".")
    _git(p, "commit", "-m", "init")
    _git(p, "branch", "fork-main")
    return p


def test_both_unset_resolve_none(tmp_path):
    # No git access needed — resolution short-circuits to None for the default.
    cfg = ProjectConfig()
    assert resolve_base_branch(cfg, tmp_path) is None
    assert resolve_merge_target(cfg, tmp_path) is None


def test_explicit_base_and_target(repo):
    cfg = ProjectConfig(worktree=WorktreeConfig(base_branch="fork-main", merge_target="fork-main"))
    assert resolve_base_branch(cfg, repo) == "fork-main"
    assert resolve_merge_target(cfg, repo) == "fork-main"


def test_base_only_target_defaults_to_base(repo):
    cfg = ProjectConfig(worktree=WorktreeConfig(base_branch="fork-main"))
    assert resolve_base_branch(cfg, repo) == "fork-main"
    assert resolve_merge_target(cfg, repo) == "fork-main"


def test_missing_base_branch_fails_loud(repo):
    cfg = ProjectConfig(worktree=WorktreeConfig(base_branch="nope"))
    with pytest.raises(WorktreeConfigError) as exc:
        resolve_base_branch(cfg, repo)
    assert "base_branch" in str(exc.value)
    assert "nope" in str(exc.value)


def test_missing_merge_target_fails_loud(repo):
    cfg = ProjectConfig(worktree=WorktreeConfig(merge_target="ghost"))
    with pytest.raises(WorktreeConfigError) as exc:
        resolve_merge_target(cfg, repo)
    assert "merge_target" in str(exc.value)
    assert "ghost" in str(exc.value)


# -- Step 5: resolve_worktree_branches reads ai-hats.yaml from project_dir --


def test_resolve_worktree_branches_reads_yaml(repo):
    from ai_hats.paths import PROJECT_CONFIG
    from ai_hats.wt_config import resolve_worktree_branches

    (repo / PROJECT_CONFIG).write_text(
        "schema_version: 4\nai_hats_dir: .agent/ai-hats\nprovider: agy\n"
        "worktree:\n  base_branch: fork-main\n  merge_target: fork-main\n"
    )
    assert resolve_worktree_branches(repo) == ("fork-main", "fork-main")


def test_resolve_worktree_branches_no_config_is_none(repo):
    from ai_hats.wt_config import resolve_worktree_branches

    # No ai-hats.yaml at all → today's behavior (both None).
    assert resolve_worktree_branches(repo) == (None, None)
