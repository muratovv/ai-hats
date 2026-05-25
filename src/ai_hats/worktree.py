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

Create-time concurrency (HATS-479)
----------------------------------
``git worktree add`` writes to repo-wide shared state (``.git/config``
for upstream tracking, ``.git/worktrees/<name>/``, ``.git/refs/heads/``),
which git does NOT serialize across processes. Per-branch locks would
miss the real failure mode (two creates on *different* branches both
contend on ``.git/config.lock`` — see Anthropic claude-code #34645).

Defense is layered:

* **L1** — :func:`_acquire_create_lock` (repo-scoped mutex at
  ``<state_dir>/.git-worktree-create.lock``) wraps the entire
  ``load_for_branch → git worktree add → save_state`` critical section.
  Serializes ai-hats vs. ai-hats writes.
* **L2** — TOCTOU re-check of :meth:`WorktreeManager.load_for_branch`
  under L1; raises :class:`WorktreeCreateError` if the branch was
  created by a concurrent ai-hats peer between the caller's pre-check
  and L1 acquisition.
* **L3** — :func:`_retry_worktree_add` retries ``git worktree add`` with
  jittered exponential backoff on transient stderr (``could not lock
  config file``, ``File exists``) caused by *external* git processes
  (IDE, manual ``git commit``) briefly holding ``.git/config.lock``.
* **L4** — :meth:`WorktreeManager.create` cleans up ``mkdtemp`` and the
  branch (only when ``not branch_existed_before``) on any
  ``CalledProcessError``, then raises :class:`WorktreeCreateError` with
  parsed stderr — never an opaque ``subprocess.CalledProcessError``.

Merge-time concurrency (HATS-481)
---------------------------------
Concurrent ``ai-hats task transition <ID> done`` on worktrees sharing
a base ref (e.g. both based on ``master``) contend on
``.git/index.lock`` when running ``git merge``. Pre-HATS-481
``state._teardown_worktree`` swallowed the resulting
``CalledProcessError`` at WARNING and let ``transition`` proceed to
``_save_task``, persisting the new DONE state despite the merge
failure — silent data loss (same class as the GitHub Merge Queue
April-2026 incident). Defense is layered:

* **L1'** — :func:`_acquire_base_branch_lock` (filelock at
  ``<state_dir>/.base-<sanitized>.lock``) wraps
  :meth:`WorktreeManager._fast_forward_merge` and
  :meth:`WorktreeManager._squash_merge`. Granularity = one writer per
  ``(project, base_ref)`` — bors / Kodiak / Mergify / GH Merge Queue
  consensus. Closes ai-hats vs. ai-hats contention; UX-fix.
* **Free win** — :meth:`WorktreeManager._git_with_ref_lock_wait`
  passes ``-c core.filesRefLockTimeout`` /
  ``-c core.packedRefsTimeout``, letting git absorb ref-lock
  contention internally without a userspace retry. Requires git
  ≥ 2.31.
* **L3'** — :func:`_retry_git_merge` retries ``git merge`` with AWS
  full-jitter exponential backoff on the broader transient stderr set
  (``unable to create``, ``index.lock``, ``another git process``,
  ``could not lock``) — covers external git writers (IDE, manual
  ``git commit``) holding ``.git/index.lock``, which has
  no git wait-flag.
* **L4'** — :meth:`state.TaskManager._teardown_worktree` re-raises
  any merge failure (except :class:`OriginalBranchMissingError`).
  ``transition`` aborts before ``_save_task``, task stays in
  ``review``. **Data-integrity guarantee — L4' alone is sufficient
  to close the silent-loss class; L1' + L3' are UX-optimization.**

Lifecycle concurrency (HATS-480)
--------------------------------
``wt merge`` and ``wt discard`` (or two parallel ``wt discard``) on the
*same* worktree branch race outside the HATS-121 state-JSON lock: that
lock is held only across millisecond-scoped JSON I/O and does NOT cover
the surrounding git operations. Repro (R-03 in HATS-476):

* A: ``wt merge task/hats-X`` → ``_check_clean`` → ``_check_drift`` →
  ``_fast_forward_merge`` (HATS-481 base-lock acquired only inside the
  ``git merge`` call, not around the whole lifecycle).
* B: ``wt discard task/hats-X`` in parallel → ``_remove_worktree`` deletes
  the dir mid-merge → either A's merge fails ("branch deleted") or B's
  ``branch -D`` silently swallows "not fully merged" at DEBUG.
* Either way: half-merged commit on ``master`` or branch graveyard,
  state JSON cleared exactly once (second ``_clear_state`` no-ops).

* **LC** — :func:`_acquire_lifecycle_lock` (per-wt-branch filelock at
  ``<state>.json.lifecycle.lock``) wraps the entire ``merge()`` /
  ``discard()`` / ``cleanup()`` body. After acquisition the caller
  checks ``self.worktree_path.exists()`` — peer's ``_remove_worktree``
  is the irreversible event, and the directory's absence is the cheap,
  reliable signal that the lifecycle is already done; late arrival
  no-ops idempotently. Separate file from :func:`_lock_path` so a long
  lifecycle op does NOT block millisecond-scoped state-JSON I/O on
  peers (``wt list`` / ``load_for_branch`` stay snappy).

Lock ordering hierarchy across HATS-121/479/480/481 (always outer →
inner, no inversion → no deadlock):

1. ``<state>.json.lifecycle.lock``       — HATS-480 (per wt branch)
2. ``<state_dir>/.base-<base>.lock``     — HATS-481 (per base ref)
3. ``<state_dir>/.git-worktree-create.lock`` — HATS-479 (repo-wide, create-only)
4. ``<state>.json.lock``                  — HATS-121 (per state JSON, I/O only)

The lock file ``<state_dir>``  **must reside on a local filesystem**.
``filelock.FileLock`` (``fcntl`` advisory) is unreliable on NFS / SMB.
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

import filelock

from .paths import worktree_state_path, worktrees_dir

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 10.0  # seconds — see module docstring

# HATS-479 — create-time concurrency (see module docstring "Create-time concurrency")
CREATE_LOCK_TIMEOUT = 10.0       # L1: repo-scoped mutex acquisition
GIT_RETRY_MAX = 5                 # L3: 1 initial + 4 retries
GIT_RETRY_BASE_DELAY = 0.05       # 50 ms, exponential up to GIT_RETRY_MAX_DELAY
GIT_RETRY_MAX_DELAY = 0.8         # cap per-attempt delay so 5 retries finish < 4 s
CREATE_LOCK_CONTENTION_WARN = 1.0  # log at WARNING if acquisition took longer

# HATS-481 — base-branch merge serialization (see module docstring "Merge-time concurrency")
BASE_LOCK_TIMEOUT = 15.0          # L1' acquisition cap — covers a ~20-way pile-up
MERGE_RETRY_MAX = 8                # AWS canonical at our scale
MERGE_RETRY_BASE_DELAY = 0.1       # 100 ms — matches git's core.*LockTimeout default
MERGE_RETRY_MAX_DELAY = 5.0        # 5 s cap; longer wait = real work, not contention
REF_LOCK_TIMEOUT_MS = 5000         # passed to git as core.filesRefLockTimeout — covers
                                   # ref-lock contention for free (no index.lock equivalent)

# HATS-480 — per-branch lifecycle serialization (see module docstring "Lifecycle concurrency")
LIFECYCLE_LOCK_TIMEOUT = 60.0     # covers fetch + merge + remove + branch -D end-to-end


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


def _create_lock_path(project_dir: Path) -> Path:
    """Repo-scoped create-lock file path (HATS-479 L1)."""
    return worktrees_dir(project_dir) / ".git-worktree-create.lock"


@contextmanager
def _acquire_create_lock(project_dir: Path) -> Iterator[None]:
    """Hold the repo-scoped create-mutex for the wt-create critical section.

    HATS-479 L1. See module docstring "Create-time concurrency".

    Serializes ai-hats vs. ai-hats writes to ``.git/config``, ``.git/refs``,
    ``.git/worktrees/``. Does NOT protect against external git processes
    (IDE, manual ``git commit``) — :func:`_retry_worktree_add` covers that.
    """
    lock_path = _create_lock_path(project_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=CREATE_LOCK_TIMEOUT)
    t0 = time.monotonic()
    try:
        with lock:
            waited = time.monotonic() - t0
            if waited > CREATE_LOCK_CONTENTION_WARN:
                logger.warning(
                    "wt create lock acquired after %.2fs (contention)", waited
                )
            yield
    except filelock.Timeout as exc:
        raise WorktreeLockError(
            f"wt create lock held by another process for "
            f">{CREATE_LOCK_TIMEOUT:.0f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a stuck ai-hats process — check: ps aux | grep ai-hats\n"
            f"  If safe, remove the lock file and retry."
        ) from exc


def _base_lock_key(base_branch: str) -> str:
    """Sanitize a base branch name for filelock filename use (HATS-481).

    Mirrors :func:`_state_key` so the same name conventions apply:
    ``master`` → ``master``; ``feat/foo`` → ``feat-foo``; ``Develop`` → ``develop``.
    """
    return base_branch.replace("/", "-").lower()


def _base_lock_path(project_dir: Path, base_branch: str) -> Path:
    """Sibling lock file for a base ref (HATS-481 L1')."""
    return worktrees_dir(project_dir) / f".base-{_base_lock_key(base_branch)}.lock"


@contextmanager
def _acquire_base_branch_lock(
    project_dir: Path, base_branch: str, *, timeout: float = BASE_LOCK_TIMEOUT
) -> Iterator[None]:
    """Serialize merges into the same base ref (HATS-481 L1').

    Granularity = one writer per ``(project, base_ref)``. Two merges into
    ``master`` serialize; merge into ``master`` + merge into ``develop`` run
    in parallel. Matches industry consensus for merge serialization
    (bors / Kodiak / Mergify — single sequencer per base).

    Does NOT protect against external git writers (IDE, manual
    ``git commit``) — :func:`_retry_git_merge` covers those.

    :param timeout: lock acquisition timeout in seconds. Defaults to
        :data:`BASE_LOCK_TIMEOUT`. Tests override with a small value to
        provoke the timeout path deterministically.
    :raises WorktreeLockError: lock not acquired within ``timeout`` seconds.
    """
    lock_path = _base_lock_path(project_dir, base_branch)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    t0 = time.monotonic()
    try:
        with lock:
            waited = time.monotonic() - t0
            if waited > CREATE_LOCK_CONTENTION_WARN:
                logger.warning(
                    "base-branch merge lock acquired after %.2fs "
                    "(contention on '%s')",
                    waited, base_branch,
                )
            yield
    except filelock.Timeout as exc:
        raise WorktreeLockError(
            f"base-branch merge lock for '{base_branch}' held by another "
            f"process for >{timeout:.1f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a stuck ai-hats process — check: ps aux | grep ai-hats"
        ) from exc


def _lifecycle_lock_path(state_path: Path) -> Path:
    """Sibling lifecycle-lock file for a worktree state JSON (HATS-480).

    ``<state>.json`` → ``<state>.json.lifecycle.lock``. Distinct from
    :func:`_lock_path` (``.lock``) so a long ``merge()`` / ``discard()``
    body does not block millisecond-scoped state-JSON I/O on peer
    processes (``wt list`` / ``load_for_branch``).
    """
    return state_path.with_name(state_path.name + ".lifecycle.lock")


@contextmanager
def _acquire_lifecycle_lock(
    state_path: Path, *, timeout: float = LIFECYCLE_LOCK_TIMEOUT
) -> Iterator[None]:
    """Serialize destructive lifecycle ops (merge/discard) on one wt branch.

    HATS-480 closes R-03: ``wt merge`` and ``wt discard`` (or two parallel
    ``wt discard``) on the same worktree branch contend on the worktree
    dir, branch ref, and state JSON. The existing state-JSON lock
    (:func:`_acquire`) is held only across millisecond-scoped I/O and does
    NOT cover the surrounding git operations
    (``_check_clean → _check_drift → merge → _remove_worktree →
    _delete_branch → _clear_state``).

    Granularity: one writer per ``(project, wt_branch)``. Two different
    worktree branches lifecycle-operate in parallel.

    Lock ordering hierarchy (no inversion → no deadlock). The full 4-tier
    model lives in the module docstring; locally we co-hold layers 1, 2,
    and 4 — layer 3 (create-lock) is never co-held with the lifecycle
    layer because ``create()`` runs before any persisted state exists.
      1. ``<state>.json.lifecycle.lock`` — this lock (HATS-480, per wt branch)
      2. ``<state_dir>/.base-<base>.lock``     — HATS-481 (per base ref)
      4. ``<state>.json.lock``                 — HATS-121 (per state JSON)

    :param state_path: path to the worktree's state JSON. The lifecycle
        lock sits at ``<state_path>.lifecycle.lock``.
    :param timeout: lock acquisition timeout. Defaults to
        :data:`LIFECYCLE_LOCK_TIMEOUT` (60 s — covers ``fetch origin`` +
        merge + remove + ``branch -D`` end-to-end). Tests override with a
        small value to provoke the timeout path deterministically.
    :raises WorktreeLockError: lock not acquired within ``timeout`` seconds.
    """
    lock_path = _lifecycle_lock_path(state_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    t0 = time.monotonic()
    try:
        with lock:
            waited = time.monotonic() - t0
            if waited > CREATE_LOCK_CONTENTION_WARN:
                logger.warning(
                    "wt lifecycle lock acquired after %.2fs (contention on '%s')",
                    waited, state_path.name,
                )
            yield
    except filelock.Timeout as exc:
        raise WorktreeLockError(
            f"wt lifecycle lock for '{state_path.stem}' held by another "
            f"process for >{timeout:.1f}s.\n"
            f"  Lock file: {lock_path}\n"
            f"  Likely a concurrent `wt merge`/`wt discard` — "
            f"check: ps aux | grep ai-hats"
        ) from exc


# HATS-479 L3 — git stderr substrings that indicate transient contention from
# an external git writer (IDE, manual `git commit`) briefly holding
# .git/config.lock or a partially-set-up .git/worktrees/<name>/. Compared
# case-insensitively. Anything not on this list fails fast (e.g.
# "not a valid object name", "branch already exists" — those are NOT transient).
_RETRIABLE_STDERR_PATTERNS = (
    "could not lock config file",
    "file exists",
    "unable to create",
)


def _is_retriable_git_error(exc: subprocess.CalledProcessError) -> bool:
    """True iff the stderr matches a known transient-contention pattern."""
    stderr = (exc.stderr or "").lower()
    return any(p in stderr for p in _RETRIABLE_STDERR_PATTERNS)


def _retry_worktree_add(
    git_runner,
    branch: str,
    worktree_path: Path,
    *,
    sleep=time.sleep,
) -> None:
    """Run ``git worktree add -b <branch> <path>`` with bounded retry.

    HATS-479 L3. Retries only on stderr patterns from
    :data:`_RETRIABLE_STDERR_PATTERNS`. Any other error fails fast.

    :param git_runner: callable like :meth:`WorktreeManager._git`. Called as
        ``git_runner("worktree", "add", "-b", branch, str(path))``.
    :param sleep: injected for tests; defaults to :func:`time.sleep`.
    :raises subprocess.CalledProcessError: on non-retriable error, or after
        exhausting :data:`GIT_RETRY_MAX` retriable attempts.
    """
    delay = GIT_RETRY_BASE_DELAY
    last_exc: subprocess.CalledProcessError | None = None
    for attempt in range(1, GIT_RETRY_MAX + 1):
        try:
            git_runner("worktree", "add", "-b", branch, str(worktree_path))
            return
        except subprocess.CalledProcessError as exc:
            if not _is_retriable_git_error(exc):
                raise
            last_exc = exc
            if attempt == GIT_RETRY_MAX:
                break
            jitter = random.uniform(0, delay)
            logger.info(
                "git worktree add transient failure (attempt %d/%d): %s",
                attempt, GIT_RETRY_MAX,
                (exc.stderr or "").strip().splitlines()[-1] if exc.stderr else "<no stderr>",
            )
            sleep(delay + jitter)
            delay = min(delay * 2, GIT_RETRY_MAX_DELAY)
    assert last_exc is not None
    raise last_exc


def _format_git_create_error(
    exc: subprocess.CalledProcessError, branch: str
) -> str:
    """Build a human-readable message for :class:`WorktreeCreateError`.

    Special-cases the common "branch already exists" git output so that
    callers see the same message whether the collision was detected by L2
    (re-check under the create lock) or by git itself.
    """
    stderr = (exc.stderr or "").strip()
    if "already exists" in stderr.lower():
        return (
            f"Cannot create worktree on '{branch}': branch already exists.\n"
            f"  git: {stderr.splitlines()[-1] if stderr else '<no stderr>'}"
        )
    head = stderr.splitlines()[-1] if stderr else "<no stderr>"
    return (
        f"git worktree add failed for branch '{branch}'.\n"
        f"  git: {head}"
    )


# HATS-481 L3' — git stderr substrings during `git merge` that indicate
# transient contention on shared lock files (index.lock, config.lock,
# HEAD.lock, packed-refs.lock — git uses the same "Unable to create" message
# for all of them). Broader than HATS-479's set because merge touches more
# refs / files than `worktree add`. Compared case-insensitively.
_RETRIABLE_MERGE_STDERR_PATTERNS = (
    "unable to create",
    "index.lock",
    "another git process",
    "could not lock",
)


def _is_retriable_merge_error(exc: subprocess.CalledProcessError) -> bool:
    """True iff the merge stderr matches a known transient-contention pattern."""
    stderr = (exc.stderr or "").lower()
    return any(p in stderr for p in _RETRIABLE_MERGE_STDERR_PATTERNS)


def _retry_git_merge(
    git_runner,
    *git_args: str,
    sleep=time.sleep,
) -> None:
    """Run a git command (typically ``merge``) with AWS full-jitter
    exponential backoff. HATS-481 L3'.

    AWS canonical full-jitter formula:
        ``sleep_for = random.uniform(0, min(cap, base * 2 ** attempt))``

    Why full jitter (vs the equal-jitter `base + uniform(0, base)` used by
    HATS-479 :func:`_retry_worktree_add`): under heavy contention 20 agents
    all wake from index.lock release within the same millisecond. Equal
    jitter still leaves a deterministic floor; full jitter spreads them
    across the entire interval, breaking the thundering herd cleanly.

    :param git_runner: callable like :meth:`WorktreeManager._git`. Called as
        ``git_runner(*git_args)``.
    :param sleep: injected for tests; defaults to :func:`time.sleep`.
    :raises subprocess.CalledProcessError: on non-retriable error or after
        exhausting :data:`MERGE_RETRY_MAX` retriable attempts.
    """
    delay = MERGE_RETRY_BASE_DELAY
    last_exc: subprocess.CalledProcessError | None = None
    for attempt in range(1, MERGE_RETRY_MAX + 1):
        try:
            git_runner(*git_args)
            return
        except subprocess.CalledProcessError as exc:
            if not _is_retriable_merge_error(exc):
                raise
            last_exc = exc
            if attempt == MERGE_RETRY_MAX:
                break
            ceiling = min(MERGE_RETRY_MAX_DELAY, delay)
            wait = random.uniform(0, ceiling)
            cmd_label = git_args[0] if git_args else "<no-cmd>"
            logger.info(
                "git %s transient lock contention (attempt %d/%d), waiting %.2fs: %s",
                cmd_label, attempt, MERGE_RETRY_MAX, wait,
                (exc.stderr or "").strip().splitlines()[-1] if exc.stderr else "<no stderr>",
            )
            sleep(wait)
            delay = min(delay * 2, MERGE_RETRY_MAX_DELAY)
    assert last_exc is not None
    raise last_exc


class WorktreeDirtyError(Exception):
    """Raised when a destructive operation targets a worktree with uncommitted changes."""


class WorktreeLockError(Exception):
    """Raised when acquiring a worktree state lock times out (HATS-121)."""


class WorktreeCreateError(Exception):
    """Raised when ``git worktree add`` fails (after retries) or another
    ai-hats peer races to create the same branch (HATS-479).

    Wraps parsed git stderr in a human-readable message so callers (CLI,
    ``state._setup_worktree``) can surface a friendly error instead of an
    opaque ``subprocess.CalledProcessError``. Distinct from
    :class:`WorktreeLockError` (L1 mutex timeout) and
    :class:`WorktreeDirtyError` (pre-check failure).
    """


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
        or if isolation_mode is NONE (no worktree, runs in project_dir).

        HATS-479: concurrent ai-hats peers and external git writers are
        handled via L1 (repo-scoped create-mutex), L2 (TOCTOU re-check
        under the mutex), L3 (bounded retry of ``git worktree add`` on
        transient stderr) and L4 (cleanup of ``mkdtemp`` and the branch
        on failure). See module docstring "Create-time concurrency".

        :raises WorktreeCreateError: branch already exists under our
            tracked state, or ``git worktree add`` failed after retries.
            Stderr is parsed into the message; callers should NOT see an
            opaque :class:`subprocess.CalledProcessError` from here.
        :raises WorktreeLockError: L1 mutex was held by another process
            for longer than :data:`CREATE_LOCK_TIMEOUT`.
        """
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

        # HATS-479 — L1 + L2 + L4. See module docstring "Create-time concurrency".
        with _acquire_create_lock(self.project_dir):
            # L2: re-check under the lock. Closes the TOCTOU window between a
            # caller's optional pre-check and our work.
            existing = WorktreeManager.load_for_branch(
                self.project_dir, self.branch_name
            )
            if existing is not None:
                raise WorktreeCreateError(
                    f"Worktree already exists for branch "
                    f"'{self.branch_name}': {existing.worktree_path}"
                )

            # Snapshot pre-existing branch state — L4 deletes the branch on
            # failure ONLY if we created it ourselves. Without this, an
            # accidental `wt create <existing-branch>` would delete the user's
            # branch in cleanup.
            branch_existed_before = self._branch_exists(self.branch_name)

            prefix = self.branch_name.replace("/", "-")
            tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-")
            self.worktree_path = Path(tmpdir)

            try:
                _retry_worktree_add(
                    self._git, self.branch_name, self.worktree_path
                )
            except subprocess.CalledProcessError as exc:
                # L4: cleanup leaked tempdir + (only-our) branch.
                shutil.rmtree(self.worktree_path, ignore_errors=True)
                self.worktree_path = None
                if not branch_existed_before:
                    try:
                        self._git("branch", "-D", self.branch_name)
                    except subprocess.CalledProcessError:
                        pass  # branch may not have been created — fine
                raise WorktreeCreateError(
                    _format_git_create_error(exc, self.branch_name)
                ) from exc
            logger.info(
                "Created worktree %s on branch %s",
                self.worktree_path, self.branch_name,
            )
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

        HATS-480: holds a per-wt-branch lifecycle lock through the entire
        body. A concurrent ``discard()`` (or another ``merge()``) on the
        same branch waits for the lock; on acquisition we re-read the
        state JSON and no-op idempotently if a peer already cleared it.
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = (
            worktrees_dir(self.project_dir)
            / f"{_state_key(self.branch_name)}.json"
        )
        with _acquire_lifecycle_lock(state_path):
            # HATS-480 idempotency re-check: a peer (parallel discard or
            # another merge) finishing first would have run _remove_worktree
            # (dir gone) AND _clear_state (state.json gone). The worktree
            # dir is the primary signal because not all callers persist
            # state (e.g. direct WorktreeManager().create() in tests goes
            # through merge() without a save_state()). Exit cleanly so the
            # caller sees exit 0.
            if not self.worktree_path.exists():
                logger.info(
                    "Worktree '%s' already torn down by a peer — no-op",
                    self.branch_name,
                )
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
            # Match discard() / cleanup() teardown contract: a successful
            # merge invalidates self for any further lifecycle ops.
            self.worktree_path = None

    def discard(self, *, force: bool = False) -> None:
        """Remove worktree and branch without merging.

        Raises WorktreeDirtyError if the worktree has uncommitted changes
        unless force=True (HATS-062).

        HATS-480: holds the per-wt-branch lifecycle lock through the
        entire body. Parallel ``discard()`` or ``merge()`` on the same
        branch serializes; the second one observes the worktree dir
        already gone and no-ops idempotently.
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = (
            worktrees_dir(self.project_dir)
            / f"{_state_key(self.branch_name)}.json"
        )
        with _acquire_lifecycle_lock(state_path):
            # HATS-480 idempotency re-check — see merge() for the rationale.
            if not self.worktree_path.exists():
                logger.info(
                    "Worktree '%s' already torn down by a peer — no-op",
                    self.branch_name,
                )
                return

            if not force:
                self._check_clean()
            self._remove_worktree()
            self._delete_branch()
            self.worktree_path = None
            self._clear_state()

    def cleanup(self, *, force_discard: bool = False) -> None:
        """Clean up worktree. Merges changes based on isolation_mode.

        HATS-480: holds the per-wt-branch lifecycle lock through the
        entire body. A concurrent direct ``wt discard``/``wt merge`` on
        the same branch (issued by another agent / CLI while the
        context-manager is winding down) serializes against this call.
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = (
            worktrees_dir(self.project_dir)
            / f"{_state_key(self.branch_name)}.json"
        )
        with _acquire_lifecycle_lock(state_path):
            # HATS-480 idempotency re-check — see merge() / discard().
            if not self.worktree_path.exists():
                logger.info(
                    "Worktree '%s' already torn down by a peer — no-op",
                    self.branch_name,
                )
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

    def _git_with_ref_lock_wait(
        self, *args: str, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run git with ``core.filesRefLockTimeout`` / ``core.packedRefsTimeout``
        set (HATS-481 free win).

        Lets git wait for ref-lock contention internally (up to
        :data:`REF_LOCK_TIMEOUT_MS`) without burning a userspace retry
        attempt. Has no effect on ``.git/index.lock`` — that file has no
        wait-flag in git; index contention is handled by
        :func:`_retry_git_merge`.

        Requires git ≥ 2.31 (older versions ignore the ``-c`` flags silently,
        which means no help but no harm).
        """
        return self._git(
            "-c", f"core.filesRefLockTimeout={REF_LOCK_TIMEOUT_MS}",
            "-c", f"core.packedRefsTimeout={REF_LOCK_TIMEOUT_MS}",
            *args, cwd=cwd,
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
        """Squash-merge worktree branch into original branch.

        HATS-481 layered defense:
        * L1' — repo-scoped lock keyed by base ref, so two ai-hats peers
          merging into the same base serialize cleanly.
        * Free win — ``core.filesRefLockTimeout`` lets git wait on ref-locks
          internally without a userspace retry attempt.
        * L3' — :func:`_retry_git_merge` handles ``.git/index.lock``
          contention (no git wait-flag) for external git writers.
        """
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        with _acquire_base_branch_lock(self.project_dir, self._original_branch):
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "merge", "--squash", self.branch_name,
            )
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "commit", "-m", f"feat(agent): {self.branch_name}",
            )
        logger.info("Squash-merged %s into %s", self.branch_name, self._original_branch)

    def _fast_forward_merge(self) -> None:
        """Merge worktree branch with --no-ff to preserve commit history.

        HATS-481 layered defense — see :meth:`_squash_merge` for details.
        """
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        with _acquire_base_branch_lock(self.project_dir, self._original_branch):
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "merge", "--no-ff", self.branch_name,
            )
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
