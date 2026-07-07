"""Resolve worktree base_branch / merge_target from ProjectConfig (HATS-942).

The wt package (``ai_hats_wt``) stays config-agnostic; ai-hats reads the config
here and passes resolved branch names into the guard / manager. ``None`` means
"today's behavior" (cut from HEAD, merge into the HEAD-following canonical), NOT
a pinned value — that is what keeps the no-config default byte-identical.
"""

from __future__ import annotations

from pathlib import Path

from .models import ProjectConfig


class WorktreeConfigError(ValueError):
    """A configured ``worktree.base_branch`` / ``merge_target`` is absent in the repo."""


def _validate_exists(project_dir: Path, branch: str, key: str) -> None:
    from ai_hats_wt import WorktreeManager

    if not WorktreeManager.branch_exists(project_dir, branch):
        raise WorktreeConfigError(
            f"ai-hats.yaml worktree.{key} = {branch!r} does not exist in this repo. "
            f"Create it (`git branch {branch}`) or fix worktree.{key} in ai-hats.yaml."
        )


def resolve_base_branch(config: ProjectConfig, project_dir: Path) -> str | None:
    """Start-point new worktrees are cut FROM; ``None`` => cut from HEAD (today)."""
    base = config.worktree.base_branch or None
    if base is not None:
        _validate_exists(project_dir, base, "base_branch")
    return base


def resolve_merge_target(config: ProjectConfig, project_dir: Path) -> str | None:
    """Branch worktrees merge INTO; ``None`` => today's canonical set-membership.

    Falls back to ``base_branch`` when only that is set (configurable single
    trunk), else ``None``.
    """
    target = config.worktree.merge_target or config.worktree.base_branch or None
    if target is not None:
        _validate_exists(project_dir, target, "merge_target")
    return target


def resolve_worktree_branches(project_dir: Path) -> tuple[str | None, str | None]:
    """Load ai-hats.yaml from ``project_dir`` and resolve ``(base, merge_target)``.

    Both ``None`` when there is no config or no ``worktree:`` block (today's
    behavior). Fail-loud (``WorktreeConfigError``) on a configured-but-absent
    branch. A repo with no ai-hats.yaml (bare ``wt`` use) short-circuits to
    ``(None, None)``.
    """
    from .paths import PROJECT_CONFIG

    config_path = project_dir / PROJECT_CONFIG
    if not config_path.exists():
        return None, None
    config = ProjectConfig.from_yaml(config_path)
    return resolve_base_branch(config, project_dir), resolve_merge_target(config, project_dir)
