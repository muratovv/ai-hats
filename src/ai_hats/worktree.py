"""Git worktree isolation for sub-agent execution (HATS-004)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_FILE = ".agent/worktree.json"  # legacy singleton, see _migrate_singleton
STATES_DIR = ".agent/worktrees"


def _state_key(branch_name: str) -> str:
    """Derive the state file key from a branch name.

    task/hats-086 → task-hats-086
    feat/HATS-060-foo → feat-hats-060-foo
    """
    return branch_name.replace("/", "-").lower()


class IsolationMode(str, Enum):
    DISCARD = "discard"
    SQUASH = "squash"
    BRANCH = "branch"


class WorktreeManager:
    """Creates and manages isolated git worktrees.

    Two usage patterns:

    1. Context manager (sub-agents — ephemeral, auto-cleanup):
        with WorktreeManager(project_dir, "role", "sess-id") as work_dir:
            subprocess.run(..., cwd=str(work_dir))

    2. Persistent (agent CLI — create now, merge/discard later):
        mgr = WorktreeManager(project_dir, branch_name="feat/hats-004")
        mgr.create()
        mgr.save_state()
        # ... later, in another CLI call ...
        mgr = WorktreeManager.load_active(project_dir)
        mgr.merge()
    """

    def __init__(
        self,
        project_dir: Path,
        role_name: str = "",
        session_id: str = "",
        isolation_mode: IsolationMode = IsolationMode.DISCARD,
        *,
        branch_name: str = "",
    ) -> None:
        self.project_dir = project_dir
        self.role_name = role_name
        self.session_id = session_id
        self.isolation_mode = isolation_mode
        self.worktree_path: Path | None = None
        self.branch_name = branch_name or f"agent/{role_name}/{session_id}"
        self._is_git = False
        self._original_branch: str | None = None

    def create(self) -> Path:
        """Create an isolated worktree. Returns project_dir if not a git repo."""
        if not self._check_is_git():
            return self.project_dir

        self._is_git = True
        self._original_branch = self._get_current_branch()

        prefix = self.branch_name.replace("/", "-")
        tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-")
        self.worktree_path = Path(tmpdir)

        self._git("worktree", "add", "-b", self.branch_name, str(self.worktree_path))
        logger.info("Created worktree %s on branch %s", self.worktree_path, self.branch_name)
        return self.worktree_path

    def merge(self, *, squash: bool = True) -> None:
        """Merge worktree changes back into the original branch and clean up."""
        if not self._is_git or self.worktree_path is None:
            return
        try:
            if squash:
                self._squash_merge()
            else:
                self._fast_forward_merge()
        except Exception:
            logger.warning("Merge failed, branch %s preserved", self.branch_name, exc_info=True)
            self._remove_worktree()
            self._clear_state()
            raise
        self._remove_worktree()
        self._delete_branch()
        self._clear_state()

    def discard(self) -> None:
        """Remove worktree and branch without merging."""
        if not self._is_git or self.worktree_path is None:
            return
        self._remove_worktree()
        self._delete_branch()
        self.worktree_path = None
        self._clear_state()

    def cleanup(self, *, force_discard: bool = False) -> None:
        """Clean up worktree. Merges changes based on isolation_mode."""
        if not self._is_git or self.worktree_path is None:
            return

        mode = IsolationMode.DISCARD if force_discard else self.isolation_mode

        try:
            if mode == IsolationMode.SQUASH:
                self._squash_merge()
        except Exception:
            logger.warning("Merge failed, falling back to branch mode", exc_info=True)
            mode = IsolationMode.BRANCH

        # Remove worktree directory
        self._remove_worktree()

        # Delete branch unless mode is BRANCH
        if mode != IsolationMode.BRANCH:
            self._delete_branch()

        self.worktree_path = None

    def __enter__(self) -> Path:
        return self.create()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        self.cleanup(force_discard=exc_type is not None)
        return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self, *, key: str | None = None) -> Path:
        """Persist worktree state to .agent/worktrees/<key>.json."""
        k = key or _state_key(self.branch_name)
        state_dir = self.project_dir / STATES_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / f"{k}.json"
        state: dict[str, Any] = {
            "branch": self.branch_name,
            "worktree_path": str(self.worktree_path),
            "original_branch": self._original_branch,
        }
        state_path.write_text(json.dumps(state, indent=2))
        self._state_key_cached = k
        return state_path

    def _clear_state(self, *, key: str | None = None) -> None:
        k = key or getattr(self, "_state_key_cached", None) or _state_key(self.branch_name)
        state_path = self.project_dir / STATES_DIR / f"{k}.json"
        if state_path.exists():
            state_path.unlink()

    @classmethod
    def load_for_task(cls, project_dir: Path, task_id: str) -> WorktreeManager | None:
        """Load the worktree state for a specific task ID.

        Derives the key via the same _state_key used by save_state:
        task_id "HATS-086" → branch "task/hats-086" → key "task-hats-086".
        """
        key = _state_key(f"task/{task_id}")
        return cls._load_by_key(project_dir, key)

    @classmethod
    def load_for_branch(cls, project_dir: Path, branch: str) -> WorktreeManager | None:
        """Load worktree state by branch name."""
        key = _state_key(branch)
        return cls._load_by_key(project_dir, key)

    @classmethod
    def _load_by_key(cls, project_dir: Path, key: str) -> WorktreeManager | None:
        state_path = project_dir / STATES_DIR / f"{key}.json"
        if not state_path.exists():
            return None
        data = json.loads(state_path.read_text())
        wt_path = Path(data["worktree_path"])
        if not wt_path.exists():
            state_path.unlink()  # stale
            return None
        mgr = cls(project_dir, branch_name=data["branch"])
        mgr.worktree_path = wt_path
        mgr._original_branch = data.get("original_branch")
        mgr._is_git = True
        mgr._state_key_cached = key
        return mgr

    @classmethod
    def list_active(cls, project_dir: Path) -> list[WorktreeManager]:
        """Load all active worktree states. Prunes stale entries."""
        states_dir = project_dir / STATES_DIR
        if not states_dir.exists():
            return []
        result = []
        for f in sorted(states_dir.glob("*.json")):
            key = f.stem
            mgr = cls._load_by_key(project_dir, key)
            if mgr is not None:
                result.append(mgr)
        return result

    @classmethod
    def load_active(cls, project_dir: Path) -> WorktreeManager | None:
        """DEPRECATED compat shim. Returns first active worktree or None.

        Migrate callers to load_for_task / load_for_branch / list_active.
        Auto-migrates singleton .agent/worktree.json if present.
        """
        cls._migrate_singleton(project_dir)
        active = cls.list_active(project_dir)
        return active[0] if active else None

    @classmethod
    def _migrate_singleton(cls, project_dir: Path) -> None:
        """One-shot migration: .agent/worktree.json → .agent/worktrees/<key>.json."""
        old = project_dir / ".agent" / "worktree.json"
        if not old.exists():
            return
        data = json.loads(old.read_text())
        branch = data.get("branch", "")
        key = _state_key(branch)
        new_dir = project_dir / STATES_DIR
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / f"{key}.json"
        if not new_path.exists():
            new_path.write_text(old.read_text())
        old.unlink()

    @staticmethod
    def is_inside_linked_worktree(path: Path) -> bool:
        """True iff `path` is inside a git linked worktree (not the main worktree).

        Compares `git rev-parse --git-dir` against `--git-common-dir`: in the
        main worktree they resolve to the same path; in a linked worktree
        --git-dir points to .git/worktrees/<name> while --git-common-dir
        points to the canonical .git directory.

        Fail-safe: returns False on any subprocess error or non-git path.
        Caller is responsible for not blocking on this signal.
        """
        try:
            git_dir = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            common_dir = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
        if not git_dir or not common_dir:
            return False
        return Path(git_dir).resolve() != Path(common_dir).resolve()

    @staticmethod
    def list_worktrees(project_dir: Path) -> list[dict[str, str]]:
        """List all git worktrees for this project."""
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return []

        worktrees: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                if current:
                    worktrees.append(current)
                    current = {}
                continue
            if line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1].removeprefix("refs/heads/")
            elif line == "bare":
                current["bare"] = "true"
        if current:
            worktrees.append(current)
        return worktrees

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.project_dir),
            capture_output=True,
            text=True,
            check=True,
        )

    def _check_is_git(self) -> bool:
        if not (self.project_dir / ".git").exists():
            return False
        try:
            self._git("rev-parse", "--is-inside-work-tree")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_current_branch(self) -> str:
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def _squash_merge(self) -> None:
        """Squash-merge worktree branch into original branch."""
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        self._git("merge", "--squash", self.branch_name)
        self._git(
            "commit", "-m",
            f"feat(agent): {self.branch_name}",
        )
        logger.info("Squash-merged %s into %s", self.branch_name, self._original_branch)

    def _fast_forward_merge(self) -> None:
        """Regular merge of worktree branch."""
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        self._git("merge", self.branch_name)
        logger.info("Merged %s into %s", self.branch_name, self._original_branch)

    def _remove_worktree(self) -> None:
        """Remove the worktree from git and filesystem."""
        if self.worktree_path is None:
            return
        try:
            self._git("worktree", "remove", str(self.worktree_path), "--force")
        except subprocess.CalledProcessError:
            if self.worktree_path.exists():
                shutil.rmtree(self.worktree_path, ignore_errors=True)
            try:
                self._git("worktree", "prune")
            except subprocess.CalledProcessError:
                pass

    def _delete_branch(self) -> None:
        """Delete the worktree branch."""
        try:
            self._git("branch", "-D", self.branch_name)
        except subprocess.CalledProcessError:
            logger.debug("Could not delete branch %s", self.branch_name)
