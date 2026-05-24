"""Git worktree isolation for sub-agent execution (HATS-004).

Concurrency (HATS-121)
----------------------
State files in ``<ai_hats_dir>/sessions/worktrees/<key>.json`` are
guarded by per-key ``filelock.FileLock`` locks (``<state_path>.lock``).
The legacy singleton ``<ai_hats_dir>/sessions/worktree.json`` is locked
on its own path during migration. Locks are OS-level (``fcntl.flock``) —
kernel auto-releases on process death, so no stale-lock cleanup is
required.

``save_state`` writes atomically (``tmp + os.replace``) so a SIGKILL
mid-write never produces a truncated JSON.

Acquire timeout is ``LOCK_TIMEOUT`` (10s). On timeout
``WorktreeLockError`` is raised pointing the user at the lock file
and ``ps`` for diagnosis. Real operations are <50ms, so 10s is a
~200x safety margin for live-but-stuck holders.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

import filelock

from .paths import worktree_state_path, worktrees_dir

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 10.0  # seconds — see module docstring


def _state_key(branch_name: str) -> str:
    """Derive the state file key from a branch name.

    task/hats-086 → task-hats-086
    feat/HATS-060-foo → feat-hats-060-foo
    """
    return branch_name.replace("/", "-").lower()


def _lock_path(state_path: Path) -> Path:
    """Sibling lock file for a state JSON: ``<state>.json.lock``."""
    return state_path.with_name(state_path.name + ".lock")


@contextmanager
def _acquire(state_path: Path, *, timeout: float = LOCK_TIMEOUT) -> Iterator[None]:
    """Acquire an OS-level lock on ``state_path``.

    Raises :class:`WorktreeLockError` on timeout. The lock file is
    created next to ``state_path`` and is harmless to leave on disk —
    the kernel releases the actual lock on process termination.
    """
    lock_path = _lock_path(state_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    try:
        with lock:
            yield
    except filelock.Timeout as exc:
        raise WorktreeLockError(
            f"Worktree state '{state_path.name}' is locked by another "
            f"process for >{timeout:.0f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a stuck ai-hats process — check: ps aux | grep ai-hats\n"
            f"  If safe, remove the lock file and retry."
        ) from exc


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via ``tmp + os.replace`` (POSIX-atomic)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


class WorktreeDirtyError(Exception):
    """Raised when a destructive operation targets a worktree with uncommitted changes."""


class WorktreeLockError(Exception):
    """Raised when acquiring a worktree state lock times out (HATS-121)."""


class OriginalBranchMissingError(Exception):
    """Raised when worktree merge target (original branch) no longer exists.

    Worktree directory is removed on raise, but the worktree branch is
    preserved so the user can rebase + merge manually onto the current
    default branch.
    """


class WorktreeDriftError(Exception):
    """Raised when the worktree's original branch moved between create and merge.

    HATS-457 / HYP-017: the base branch SHA captured at ``wt create`` no
    longer matches the current local (or remote) tip, which means another
    agent's worktree merge — or an explicit ``git pull`` — landed commits
    that the current worktree's pre-merge verification never saw.

    Default ``wt merge`` refuses to proceed; the user re-verifies against
    the new base and re-runs with ``--accept-drift``.
    """


class IsolationMode(str, Enum):
    DISCARD = "discard"
    SQUASH = "squash"
    BRANCH = "branch"
    #: Run sub-agent in project_dir directly — no git worktree.
    #: Use only for trusted roles whose only writes go through ai-hats CLIs
    #: (e.g. reflect-session reaching `.agent/` via `ai-hats task hyp / proposal`).
    #: Trade-off: no source-tree isolation; the role is trusted to honor its
    #: scope guardrails. Required because `.agent/` is gitignored and is
    #: invisible inside a git worktree.
    NONE = "none"


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
        self._base_sha_at_create: str | None = None  # HATS-457

    def create(self) -> Path:
        """Create an isolated worktree. Returns project_dir if not a git repo
        or if isolation_mode is NONE (no worktree, runs in project_dir)."""
        if self.isolation_mode == IsolationMode.NONE:
            # No worktree: sub-agent runs directly in project_dir.
            # worktree_path stays None so cleanup() is a no-op.
            return self.project_dir
        if not self._check_is_git():
            return self.project_dir

        if not self._has_commits():
            raise RuntimeError(
                "Worktree creation requires at least one commit on HEAD, "
                "but the repository has none yet.\n"
                "  Make an initial commit first, e.g.:\n"
                "    git commit --allow-empty -m 'init'"
            )

        self._is_git = True
        self._original_branch = self._get_current_branch()
        # HATS-457: snapshot the base SHA so `wt merge` can detect drift if the
        # original branch advances between create and merge (concurrent agent
        # worktrees, manual `git pull`, etc.).
        try:
            self._base_sha_at_create = self._git(
                "rev-parse", self._original_branch
            ).stdout.strip()
        except subprocess.CalledProcessError:
            self._base_sha_at_create = None

        prefix = self.branch_name.replace("/", "-")
        tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-")
        self.worktree_path = Path(tmpdir)

        self._git("worktree", "add", "-b", self.branch_name, str(self.worktree_path))
        logger.info("Created worktree %s on branch %s", self.worktree_path, self.branch_name)
        return self.worktree_path

    def merge(
        self,
        *,
        squash: bool = False,
        force: bool = False,
        accept_drift: bool = False,
    ) -> None:
        """Merge worktree changes back into the original branch and clean up.

        Raises WorktreeDirtyError if the worktree has uncommitted changes
        unless force=True (HATS-062).

        Raises WorktreeDriftError if the original branch moved between
        worktree create and merge (locally or on the remote) unless
        accept_drift=True (HATS-457 / HYP-017). ``force`` deliberately
        does not bypass drift — the two checks address different risks
        (uncommitted changes vs stale baseline).

        Raises OriginalBranchMissingError if the original branch was deleted
        while the worktree was active. Worktree dir is removed but the
        worktree branch is preserved for manual rebase + merge (HATS-253).
        """
        if not self._is_git or self.worktree_path is None:
            return
        if not force:
            self._check_clean()
        if not accept_drift:
            self._check_drift()
        if self._original_branch and not self._branch_exists(self._original_branch):
            self._remove_worktree()
            self._clear_state()
            raise OriginalBranchMissingError(
                f"Original branch '{self._original_branch}' no longer exists. "
                f"Worktree branch '{self.branch_name}' preserved — rebase onto "
                f"the current default branch and merge manually."
            )
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

    def discard(self, *, force: bool = False) -> None:
        """Remove worktree and branch without merging.

        Raises WorktreeDirtyError if the worktree has uncommitted changes
        unless force=True (HATS-062).
        """
        if not self._is_git or self.worktree_path is None:
            return
        if not force:
            self._check_clean()
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
        """Persist worktree state to <ai_hats_dir>/sessions/worktrees/<key>.json (locked, atomic)."""
        k = key or _state_key(self.branch_name)
        state_dir = worktrees_dir(self.project_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / f"{k}.json"
        state: dict[str, Any] = {
            "branch": self.branch_name,
            "worktree_path": str(self.worktree_path),
            "original_branch": self._original_branch,
            "base_sha_at_create": self._base_sha_at_create,  # HATS-457
        }
        with _acquire(state_path):
            _atomic_write_json(state_path, state)
        self._state_key_cached = k
        return state_path

    def _clear_state(self, *, key: str | None = None) -> None:
        k = key or getattr(self, "_state_key_cached", None) or _state_key(self.branch_name)
        state_path = worktrees_dir(self.project_dir) / f"{k}.json"
        with _acquire(state_path):
            try:
                state_path.unlink()  # safe-delete: ok worktree-state (git-managed)
            except FileNotFoundError:
                pass

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
        state_path = worktrees_dir(project_dir) / f"{key}.json"
        with _acquire(state_path):
            try:
                raw = state_path.read_text()
            except FileNotFoundError:
                return None
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                # Corrupted state — best-effort cleanup, treat as absent.
                logger.warning("Corrupted worktree state at %s — removing", state_path)
                try:
                    state_path.unlink()  # safe-delete: ok worktree-state (corrupted)
                except FileNotFoundError:
                    pass
                return None
            wt_path = Path(data["worktree_path"])
            if not wt_path.exists():
                try:
                    state_path.unlink()  # safe-delete: ok worktree-state (stale)
                except FileNotFoundError:
                    pass
                return None
        mgr = cls(project_dir, branch_name=data["branch"])
        mgr.worktree_path = wt_path
        mgr._original_branch = data.get("original_branch")
        # HATS-457: legacy state files (pre-457) omit this key — graceful
        # degradation, drift check becomes a no-op.
        mgr._base_sha_at_create = data.get("base_sha_at_create")
        mgr._is_git = True
        mgr._state_key_cached = key
        return mgr

    @classmethod
    def list_active(cls, project_dir: Path) -> list[WorktreeManager]:
        """Load all active worktree states. Prunes stale entries."""
        states_dir = worktrees_dir(project_dir)
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
        Auto-migrates singleton worktree.json under <ai_hats_dir>/sessions/ if present.
        """
        cls._migrate_singleton(project_dir)
        active = cls.list_active(project_dir)
        return active[0] if active else None

    @classmethod
    def _migrate_singleton(cls, project_dir: Path) -> None:
        """One-shot migration: singleton worktree.json → per-key worktrees/<key>.json.

        Locked + idempotent: concurrent callers will serialize on the
        legacy file's lock; the second one finds the source already
        unlinked and exits cleanly. After HATS-312 the singleton lives at
        ``<ai_hats_dir>/sessions/worktree.json`` and per-key files under
        ``<ai_hats_dir>/sessions/worktrees/`` — the filesystem move from
        ``.agent/`` is handled separately by ``Assembler._migrate_layout_v4_sessions``.
        """
        old = worktree_state_path(project_dir)
        with _acquire(old):
            try:
                raw = old.read_text()
            except FileNotFoundError:
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Corrupted legacy worktree.json at %s — leaving in place", old)
                return
            branch = data.get("branch", "")
            key = _state_key(branch)
            new_dir = worktrees_dir(project_dir)
            new_dir.mkdir(parents=True, exist_ok=True)
            new_path = new_dir / f"{key}.json"
            with _acquire(new_path):
                if not new_path.exists():
                    _atomic_write_json(new_path, data)
            try:
                # Data already preserved at new_path — duplicate cleanup,
                # no recovery value in a snapshot. Whitelist.
                old.unlink()  # safe-delete: ok layout-migration duplicate
            except FileNotFoundError:
                pass

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

    def _check_clean(self) -> None:
        """Raise WorktreeDirtyError if the worktree has uncommitted changes."""
        if self.worktree_path is None:
            return
        try:
            result = self._git("status", "--porcelain", cwd=self.worktree_path)
        except subprocess.CalledProcessError:
            return  # can't check — don't block
        if result.stdout.strip():
            raise WorktreeDirtyError(
                f"Worktree '{self.branch_name}' has uncommitted changes.\n"
                f"  Path: {self.worktree_path}\n"
                f"  Commit your work first, or use --force to discard anyway."
            )

    def _check_is_git(self) -> bool:
        if not (self.project_dir / ".git").exists():
            return False
        try:
            self._git("rev-parse", "--is-inside-work-tree")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _has_commits(self) -> bool:
        try:
            self._git("rev-parse", "--verify", "HEAD")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _get_current_branch(self) -> str:
        result = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    def _branch_exists(self, name: str) -> bool:
        try:
            self._git("rev-parse", "--verify", "--quiet", name)
            return True
        except subprocess.CalledProcessError:
            return False

    # ------------------------------------------------------------------
    # Drift detection (HATS-457 / HYP-017)
    # ------------------------------------------------------------------

    _DRIFT_PATH_LIMIT = 50  # max paths printed inline; overflow → "… N more"

    def _check_drift(self) -> None:
        """Raise WorktreeDriftError if the original branch moved since create.

        Drift sources:
          * local: another worktree's `wt merge` advanced the local
            original branch.
          * remote: someone pushed commits to ``origin/<base>`` that the
            local branch has not pulled.

        Best-effort ``git fetch origin <base>`` runs first to surface
        remote drift. Network/no-remote failures are swallowed so an
        offline merge still proceeds with the local check.

        Skips silently when the saved ``base_sha_at_create`` is missing
        (legacy state file from before HATS-457).
        """
        if self._base_sha_at_create is None or self._original_branch is None:
            return

        # Best-effort fetch — silent on failure (no remote, offline, etc.).
        try:
            self._git("fetch", "origin", self._original_branch)
        except subprocess.CalledProcessError:
            logger.debug(
                "Drift check: fetch origin %s failed, falling back to local-only check",
                self._original_branch,
            )

        try:
            current_local = self._git(
                "rev-parse", self._original_branch
            ).stdout.strip()
        except subprocess.CalledProcessError:
            # Can't read the original branch SHA — let the missing-branch
            # path in merge() handle it.
            return

        current_remote: str | None
        try:
            current_remote = self._git(
                "rev-parse", "--verify", "--quiet", f"origin/{self._original_branch}"
            ).stdout.strip()
        except subprocess.CalledProcessError:
            current_remote = None

        local_drifted = current_local != self._base_sha_at_create
        remote_drifted = (
            current_remote is not None
            and current_remote != current_local
        )

        if not local_drifted and not remote_drifted:
            return

        lines = [
            f"Worktree base '{self._original_branch}' drifted since worktree was created."
        ]
        if local_drifted:
            n, paths = self._drift_summary(self._base_sha_at_create, current_local)
            lines.append(
                f"  local: {self._short(self._base_sha_at_create)} → "
                f"{self._short(current_local)} ({n} commit{'s' if n != 1 else ''} ahead)"
            )
            if paths:
                lines.append("  affected paths (local drift):")
                lines.extend(f"    {p}" for p in paths)
        if remote_drifted:
            assert current_remote is not None
            n_r, paths_r = self._drift_summary(current_local, current_remote)
            lines.append(
                f"  remote: origin/{self._original_branch} is "
                f"{n_r} commit{'s' if n_r != 1 else ''} ahead of local"
            )
            if paths_r:
                lines.append("  affected paths (remote drift):")
                lines.extend(f"    {p}" for p in paths_r)
        lines.append(
            "Re-verify your changes against the new base, then re-run with --accept-drift."
        )

        raise WorktreeDriftError("\n".join(lines))

    def _drift_summary(self, base: str, head: str) -> tuple[int, list[str]]:
        """Return (commit count, capped affected-path list) for base..head."""
        try:
            n_str = self._git("rev-list", "--count", f"{base}..{head}").stdout.strip()
            n = int(n_str) if n_str else 0
        except (subprocess.CalledProcessError, ValueError):
            n = 0
        try:
            diff = self._git("diff", "--name-only", f"{base}..{head}").stdout
        except subprocess.CalledProcessError:
            diff = ""
        paths = [line for line in diff.splitlines() if line.strip()]
        if len(paths) > self._DRIFT_PATH_LIMIT:
            overflow = len(paths) - self._DRIFT_PATH_LIMIT
            # Use a marker that obviously isn't a path (parentheses + word
            # "files"), so the operator can't mistake the cap line for a
            # real filename.
            paths = paths[: self._DRIFT_PATH_LIMIT] + [f"(… {overflow} more files)"]
        return n, paths

    @staticmethod
    def _short(sha: str) -> str:
        return sha[:8] if sha else "?"

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
        """Merge worktree branch with --no-ff to preserve commit history."""
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        self._git("merge", "--no-ff", self.branch_name)
        logger.info("Merged %s into %s", self.branch_name, self._original_branch)

    def _remove_worktree(self) -> None:
        """Remove the worktree from git and filesystem."""
        if self.worktree_path is None:
            return
        try:
            self._git("worktree", "remove", str(self.worktree_path), "--force")
        except subprocess.CalledProcessError:
            if self.worktree_path.exists():
                # Worktree removal fallback: git failed, we force-clean.
                # Worktree contents are user code, but the worktree was
                # already meant to be torn down via `git worktree remove`
                # which would have nuked it anyway. Whitelist.
                shutil.rmtree(self.worktree_path, ignore_errors=True)  # safe-delete: ok git-worktree-teardown
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
