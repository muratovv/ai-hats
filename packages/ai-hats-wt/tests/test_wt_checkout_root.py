"""HATS-1632 — where ``create()`` mints the worktree.

The engine takes the checkout root as an injected path-base (ADR-0013 D4, the
same shape as ``state_dir``) so it never imports ``ai_hats.paths``. Injected: the
tree lands there. Not injected: the historical ``tempfile.mkdtemp`` fallback, so a
bare-core consumer (D9) keeps working unchanged.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from ai_hats_wt import NOOP_LIFECYCLE, WorktreeManager

pytestmark = pytest.mark.integration


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "dev@example.com")
    _git(project, "config", "user.name", "Dev")
    (project / "README.md").write_text("# proj\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def test_create_mints_under_the_injected_checkout_root(repo: Path, tmp_path: Path) -> None:
    """The injected root decides the location — not ``$TMPDIR``."""
    checkouts = tmp_path / "checkouts"
    mgr = WorktreeManager(
        repo,
        branch_name="task/probe",
        lifecycle=NOOP_LIFECYCLE,
        worktree_checkouts_dir=checkouts,
    )

    wt_path = mgr.create()

    assert wt_path.parent.resolve() == checkouts.resolve()
    assert not wt_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    mgr.discard()


def test_create_falls_back_to_the_temp_root_when_not_injected(repo: Path) -> None:
    """D9 bare core: no injection, historical behaviour, no ai-hats import."""
    mgr = WorktreeManager(repo, branch_name="task/bare", lifecycle=NOOP_LIFECYCLE)

    wt_path = mgr.create()

    assert wt_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    mgr.discard()
