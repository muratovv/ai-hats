"""ADR-0013 P2 (HATS-850) — state_dir path-base injection seam.

Pins the D4 contract: an injected ``state_dir`` is where the manager's
state-JSON and locks land (fail-under-revert — revert the injection and they
land under ``worktrees_dir`` instead), and the R5 ``__debug__`` backstop fires
when an ai-hats driver (a non-no-op lifecycle) omits the base.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_hats.paths import worktrees_dir
from ai_hats.wt import NOOP_LIFECYCLE, WorktreeManager
from ai_hats.wt.locks import _state_key
from ai_hats.wt_lifecycle import HOOK_LIFECYCLE


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.email", "test@test.com")
    _git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("# Test")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")
    return project


def test_injected_state_dir_drives_state_path(git_project, tmp_path):
    """The injected state_dir is where state JSON lands — NOT ``worktrees_dir``.
    Fail-under-revert: hardcode ``worktrees_dir`` in the core and ``custom`` stays
    empty while the convention dir fills.

    (The lock path derives from the same ``_state_dir`` — covered without an
    on-disk check by ``test_base_lock_path_under_state_dir``; ``filelock`` may
    remove the lock file on release, so we do not assert its persistence here.)
    """
    custom = tmp_path / "custom_state"
    mgr = WorktreeManager(git_project, branch_name="task/seam", state_dir=custom)
    mgr.create()
    mgr.save_state()

    assert mgr._state_dir == custom
    assert (custom / f"{_state_key('task/seam')}.json").exists()
    # The ai-hats convention dir was never written through this manager.
    convention = worktrees_dir(git_project)
    assert not convention.exists() or not list(convention.glob("*.json"))


def test_load_honors_injected_state_dir(git_project, tmp_path):
    """A load resolves the same base it was given — and the bare-core fallback
    (``.wt``) does not see custom-dir state (injection isolation)."""
    custom = tmp_path / "custom_state"
    mgr = WorktreeManager(git_project, branch_name="task/seam", state_dir=custom)
    mgr.create()
    mgr.save_state()

    assert WorktreeManager.load_for_branch(git_project, "task/seam") is None
    loaded = WorktreeManager.load_for_branch(git_project, "task/seam", state_dir=custom)
    assert loaded is not None
    assert loaded._state_dir == custom


def test_r5_assert_fires_when_ai_hats_driver_omits_state_dir(git_project):
    """A non-no-op lifecycle (an ai-hats driver) MUST be given an explicit base;
    omitting it would silently de-serialize the cross-process locks (ADR-0006)."""
    with pytest.raises(AssertionError):
        WorktreeManager(git_project, branch_name="task/x", lifecycle=HOOK_LIFECYCLE)


def test_r5_assert_quiet_for_bare_core(git_project):
    """Bare core (no-op lifecycle, no state_dir) falls back project-local — no
    raise — so a standalone consumer needs no path wiring."""
    mgr = WorktreeManager(git_project, branch_name="task/x", lifecycle=NOOP_LIFECYCLE)
    assert mgr._state_dir == git_project / ".wt"


def test_lifecycle_ctx_threads_state_dir_into_hook_log_dir(git_project, tmp_path):
    """ADR-0013 D4 / HATS-851 (R4): the manager threads its injected ``state_dir``
    into ``LifecycleContext``, and the hook bundle resolves hook-logs off it — so a
    custom-base HOOK_LIFECYCLE driver keeps state + hook-logs under ONE base instead
    of splitting (state at the custom base, logs under ``worktrees_dir(project_dir)``).

    Fail-under-revert: drop the ``LifecycleContext.state_dir`` field (→ AttributeError)
    or re-base ``_wt_hook_log_dir`` on ``worktrees_dir(project_dir)`` (→ the log dir no
    longer sits under ``custom``).
    """
    from ai_hats.wt_lifecycle import _wt_hook_log_dir

    custom = tmp_path / "custom_state"
    mgr = WorktreeManager(
        git_project, branch_name="task/hats-851", lifecycle=HOOK_LIFECYCLE, state_dir=custom
    )
    ctx = mgr._lifecycle_ctx()

    assert ctx.state_dir == custom
    log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
    assert log_dir == custom / f"{_state_key('task/hats-851')}.logs"
    # NOT split under the ai-hats convention dir — the regression this fix prevents.
    assert worktrees_dir(git_project) not in log_dir.parents
