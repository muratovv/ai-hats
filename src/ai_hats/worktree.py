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

Operator-error guards (HATS-482)
--------------------------------
Concurrency hardening (HATS-479/480/481) closed the data-loss class.
HATS-482 layers a set of operator-visibility guards on top, so that
remaining single-actor mistakes fail loud instead of corrupting state:

* **B-02** — :meth:`WorktreeManager._delete_branch` classifies known
  ``git branch -D`` failures (``not fully merged``, ``used by worktree``,
  ``cannot lock ref``) and raises :class:`WorktreePartialCleanupError`.
  CLI handlers convert this to ``exit 2`` with manual-cleanup guidance.
  Unclassified stderr stays silent at DEBUG (regression-safe).
* **B-07** — :func:`_state_key` is case-preserving. Pre-482 keys were
  lowercased, collapsing distinct git refs (``Task/X`` ↔ ``task/x``)
  onto one state file. Legacy lowercased files migrate one-shot in
  :meth:`WorktreeManager._load_by_key` under the state lock.
* **B-08** — :func:`ai_hats.cli._helpers._guard_not_inside_linked_worktree`
  is wired into ``wt create / merge / discard / list``. Refuses to
  resolve ``_project_dir`` upward through ``/tmp`` when CWD is inside
  a linked worktree, preventing state writes to a tmp tree.
  ``wt exec`` / ``wt env`` are intentionally exempt (designed to run
  from inside the worktree).
* **R-08** — :func:`ai_hats.cli.worktree._resolve_worktree` raises
  :class:`click.UsageError` when no branch is given AND ``>1`` worktree
  is tracked, instead of silently picking alphabetical first.

Teardown hardening (HATS-488)
-----------------------------
* **B-03** — :meth:`WorktreeManager._remove_worktree` no longer falls
  back to ``shutil.rmtree`` (``ignore_errors=True``) when ``git worktree
  remove --force`` fails. Default raises :class:`WorktreeRemoveError`
  (data preservation); ``wt discard --force-remove`` opts in to the
  rmtree path explicitly. ``wt merge`` propagates the exception so
  the operator sees the residual dir.
* **R-04** — auto-``git worktree prune`` in the same fallback was
  dropped. Pruning could race with concurrent ``wt create`` (admin
  entry unlinked before target dir materializes); the trade is
  occasional orphan ``.git/worktrees/<name>/`` admin entries that
  ``wt list`` surfaces and operators clean with manual
  ``git worktree prune``.
* **B-06** — :meth:`WorktreeManager.is_inside_linked_worktree` now
  runs ONE ``git rev-parse --git-dir --git-common-dir`` instead of
  two separate ``subprocess.run`` calls. Closes the race window
  between the two forks (path comparison saw mismatched paths if
  ``.git`` was renamed between calls) and is incidentally faster.

Stale-lock observability (HATS-486)
-----------------------------------
``.git/index.lock`` left behind by a crashed git process (manual SIGKILL,
OOM kill, system crash mid-merge) blocks every subsequent merge. Git's
own message ("Another git process seems to be running") suggests manual
``rm -f`` but gives no confidence signal — the operator can't tell from
the message whether a live process holds the lock or whether it's just
debris.

* **v1 (this layer)** — :func:`_stale_index_lock_age` is probed inside
  :func:`_retry_git_merge` on the FIRST retriable error. When the lock
  is older than :data:`STALE_INDEX_LOCK_THRESHOLD_S` (60 s),
  ``logger.warning`` emits the absolute path + age + the exact
  ``rm -f`` command the operator should run. Surfaces the actionable
  hint before retry exhaustion (~30 s for the 8-attempt cycle).
  **Warn-only** — no auto-delete; v2 will revisit after the warning
  has been observed in production logs.
* Limited to ``.git/index.lock`` — other lockfiles (``config.lock``,
  ``HEAD.lock``, ``packed-refs.lock``) are absorbed inside git via
  the ``core.filesRefLockTimeout`` / ``core.packedRefsTimeout`` flags
  HATS-481 already passes. Only ``index.lock`` has no wait-flag in
  git and is the empirical source of mid-merge-crash pain.

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

# HATS-486 — stale .git/index.lock observability (see module docstring
# "Stale-lock observability"). Threshold above which the lock is treated
# as evidence of a crashed git process (warn-only — no auto-delete in v1).
STALE_INDEX_LOCK_THRESHOLD_S = 60.0


def _stale_index_lock_age(
    project_dir: Path,
    threshold_s: float = STALE_INDEX_LOCK_THRESHOLD_S,
) -> tuple[float, Path] | None:
    """Return ``(age_seconds, lock_path)`` if ``.git/index.lock`` exists
    AND is older than ``threshold_s``; else ``None``.

    HATS-486 v1: warn-only — caller decides what to do with the
    information. No file mutation.

    Uses ``git rev-parse --git-common-dir`` so both main and linked
    worktrees resolve to the same index.lock path (linked worktrees
    don't have their own index.lock — git serializes against the
    common .git/index.lock).

    Returns ``None`` on:
      * git binary missing / not a git repo (caller is in a tmp tree);
      * ``.git/index.lock`` doesn't exist (happy path);
      * lock exists but age below threshold (legit in-progress merge).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    common_dir = Path(result.stdout.strip())
    lock_path = common_dir / "index.lock"
    try:
        st = lock_path.stat()
    except FileNotFoundError:
        return None
    age = time.time() - st.st_mtime
    if age < threshold_s:
        return None
    return age, lock_path


def _state_key(branch_name: str) -> str:
    """Derive the state file key from a branch name.

    task/hats-086 → task-hats-086
    feat/HATS-060-foo → feat-HATS-060-foo

    HATS-482 (B-07): case-preserving. Pre-482 keys were lowercased, which
    collided distinct git refs (``Task/X`` ↔ ``task/x``) onto one state
    file. Git refs are case-sensitive (modulo filesystem); state keys must
    match git reality. Legacy lowercased state files on disk are migrated
    on first lookup — see :meth:`WorktreeManager._load_by_key`.
    """
    return branch_name.replace("/", "-")


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
    create_branch: bool = True,
    sleep=time.sleep,
) -> None:
    """Run ``git worktree add [-b] <branch> <path>`` with bounded retry.

    HATS-479 L3. Retries only on stderr patterns from
    :data:`_RETRIABLE_STDERR_PATTERNS`. Any other error fails fast.

    :param git_runner: callable like :meth:`WorktreeManager._git`. Called as
        ``git_runner("worktree", "add", "-b", branch, str(path))`` when
        ``create_branch`` is ``True`` (default — the original happy path,
        branch does not exist yet), or
        ``git_runner("worktree", "add", str(path), branch)`` when
        ``create_branch`` is ``False`` (HATS-517 Case A — branch already
        exists and we attach it to a new linked worktree).
    :param create_branch: ``True`` to pass ``-b <branch>`` (creates the
        branch). ``False`` to attach an existing branch to a new worktree
        (positional ``<path> <branch>``).
    :param sleep: injected for tests; defaults to :func:`time.sleep`.
    :raises subprocess.CalledProcessError: on non-retriable error, or after
        exhausting :data:`GIT_RETRY_MAX` retriable attempts.
    """
    if create_branch:
        cmd_args = ("worktree", "add", "-b", branch, str(worktree_path))
    else:
        # `git worktree add <path> <branch>` — attaches the existing branch.
        cmd_args = ("worktree", "add", str(worktree_path), branch)
    delay = GIT_RETRY_BASE_DELAY
    last_exc: subprocess.CalledProcessError | None = None
    for attempt in range(1, GIT_RETRY_MAX + 1):
        try:
            git_runner(*cmd_args)
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
    project_dir: Path | None = None,
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
    :param project_dir: if provided, on the FIRST retriable error we probe
        ``.git/index.lock`` for staleness (HATS-486 v1, warn-only). When
        the lock is older than :data:`STALE_INDEX_LOCK_THRESHOLD_S`,
        logger.warning emits the path + age + ``rm -f`` recommendation so
        the operator can intervene without waiting for retry exhaustion.
        Pass ``None`` (default) to skip the probe — callers in code paths
        that don't touch index.lock don't need it.
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
            # HATS-486: probe stale index.lock on FIRST retriable error so
            # the operator sees the actionable hint BEFORE retry exhaustion
            # (~30s for 8 attempts at full-jitter ceiling). Only on attempt
            # 1 — repeated probes would spam.
            if attempt == 1 and project_dir is not None:
                stale = _stale_index_lock_age(project_dir)
                if stale is not None:
                    age, lock_path = stale
                    logger.warning(
                        ".git/index.lock is %.0fs old (threshold %.0fs) — "
                        "likely stale from a crashed git process. If no live "
                        "git is running, manually clean with: rm -f %s "
                        "(see HATS-486)",
                        age, STALE_INDEX_LOCK_THRESHOLD_S, lock_path,
                    )
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


class WorktreePartialCleanupError(Exception):
    """Raised when ``_delete_branch`` cannot delete the worktree branch and
    the failure has a known, operator-actionable cause (HATS-482 / B-02).

    Pre-482 ``_delete_branch`` swallowed every ``CalledProcessError`` at
    DEBUG level, producing a silent "branch graveyard" (especially for
    "not fully merged" — a data-loss WARNING). Now classified causes are
    surfaced so the CLI can exit non-zero with guidance.

    Carries:
      * ``branch_name`` — the branch git refused to delete.
      * ``reason`` — one of ``"not_fully_merged"``, ``"checked_out"``,
        ``"locked"``. Unknown stderr stays silent at DEBUG (regression-safe).
      * ``stderr_tail`` — last line of git's stderr for diagnostics.

    Lifecycle contract: ``_delete_branch`` runs AFTER ``_remove_worktree``
    and BEFORE ``_clear_state`` in merge()/discard()/cleanup(). Raising
    here leaves worktree dir gone, branch alive, state JSON intact — the
    operator can re-attempt cleanup after addressing the underlying cause.
    """

    def __init__(self, branch_name: str, reason: str, stderr_tail: str) -> None:
        self.branch_name = branch_name
        self.reason = reason
        self.stderr_tail = stderr_tail
        super().__init__(
            f"Branch '{branch_name}' could not be deleted ({reason}): {stderr_tail}"
        )


class WorktreeRemoveError(Exception):
    """``_remove_worktree`` could not delete the worktree directory.

    HATS-488 / B-03: pre-488 the rmtree fallback ran with
    ``ignore_errors=True`` whenever ``git worktree remove --force``
    failed — silently nuking uncommitted work the operator hadn't
    explicitly OK'd. Post-488 the rmtree path is opt-in via
    ``force_rmtree=True`` (CLI flag ``--force-remove`` on
    ``wt discard``); the default path raises this exception so the
    caller (and ultimately the operator) knows the worktree dir is
    still on disk.

    Carries ``path`` (the directory still on disk) and ``stderr_tail``
    (last line of git's error output).
    """

    def __init__(self, path: Path, stderr_tail: str) -> None:
        self.path = path
        self.stderr_tail = stderr_tail
        super().__init__(
            f"git worktree remove failed and the directory is still on disk: "
            f"{path}\n  git: {stderr_tail}"
        )


class OriginalBranchMissingError(Exception):
    """Raised when worktree merge target (original branch) no longer exists.

    Worktree directory is removed on raise, but the worktree branch is
    preserved so the user can rebase + merge manually onto the current
    default branch.
    """


class WorktreeStateLostError(Exception):
    """Raised by ``_teardown_worktree`` when a ``transition done`` would
    silently no-op despite an un-merged worktree branch still existing.

    HATS-541: whenever the worktree ``state.json`` is gone but the
    worktree branch still exists, a ``task transition <id> done`` would
    resolve ``WorktreeManager.load_for_task`` to ``None`` and silently
    mark the task DONE without performing any merge — a silent-data-loss
    class of bug (same shape as the GitHub Merge Queue Apr-2026 incident
    that HATS-481 fixed in a sibling code path).

    HATS-587 note: the original trigger was ``Worktree.merge()`` clearing
    state + removing the worktree dir on merge failure. F5 changed that —
    a failed merge now preserves worktree + state + branch for a clean
    retry, so this guard is no longer reachable via the failed-merge path.
    It remains as defense-in-depth for the residual orphan causes: manual
    deletion of the state JSON, a crash between ``_remove_worktree`` and
    ``_clear_state`` on the SUCCESS path, and pre-587 orphans.

    Carries ``task_id`` + ``branch_name`` so the CLI handler can build
    a recovery-hint message. The exception itself does NOT mutate any
    state — caller decides how to surface it.
    """

    def __init__(self, task_id: str, branch_name: str) -> None:
        self.task_id = task_id
        self.branch_name = branch_name
        super().__init__(
            f"Worktree state for {task_id} is missing, but branch "
            f"'{branch_name}' still exists with un-merged commits."
        )


class WorktreeDriftError(Exception):
    """Raised when the worktree's original branch moved between create and merge.

    HATS-457 / HYP-017: the base branch SHA captured at ``wt create`` no
    longer matches the current local (or remote) tip, which means another
    agent's worktree merge — or an explicit ``git pull`` — landed commits
    that the current worktree's pre-merge verification never saw.

    Default ``wt merge`` refuses to proceed; the user re-verifies against
    the new base and re-runs with ``--accept-drift``.

    **Body contract (HATS-509)**: the exception message carries
    **facts only** — the drift summary built by ``_check_drift``
    (header, ``local:`` / ``remote:`` sections, ``affected paths:``
    listings). It MUST NOT include user-facing recipe text such as
    "re-run with ``--accept-drift``". The recipe is owned by CLI
    handlers (``cli/worktree.py wt_merge``, ``cli/task.py
    task_transition``) so each command surface can name its own flags
    — historically the literal trailer leaked into ``task transition
    done``, where the flag does NOT exist.
    """


class WorktreeBaseBranchError(Exception):
    """Raised when ``wt create`` is invoked with main-repo HEAD not on a
    canonical base branch (``master`` / ``main``).

    HATS-518: ``WorktreeManager.create()`` captures the main repo's current
    branch as ``_original_branch`` — which becomes the merge target of
    ``wt merge``. If the operator parked the main repo on a feature branch
    (e.g. ``task/hats-510``) before invoking ``wt create`` or
    ``task transition <ID> execute``, subsequent merges silently land on
    that feature branch instead of master. See incident report on HATS-486.

    Recovery: ``git checkout <canonical-base>`` in the main repo, then
    re-run the command.

    **`--force` does NOT bypass this guard.** ``task transition --force``
    overrides the FSM (state-machine arrow), not the safety contract —
    same as merge / discard refusals (HATS-481). If the operator genuinely
    wants the worktree to merge into a non-canonical branch, they checkout
    that branch in the main repo first; ``--force`` is not the lever.
    """

    def __init__(self, current: str, canonical: list[str]) -> None:
        self.current = current
        self.canonical = canonical
        super().__init__(
            f"Refused: main repo HEAD is '{current}', not a canonical base "
            f"branch ({', '.join(canonical)}). Worktrees inherit their merge "
            f"target from the current branch — creating one from a feature "
            f"branch leads to merges landing on that feature branch, not "
            f"master (HATS-518). Run `git checkout <base>` in the main repo "
            f"first, then retry."
        )


class WorktreeBaseBranchMismatchError(Exception):
    """Raised when ``wt merge`` runs with main-repo HEAD on a branch other
    than ``_original_branch`` (the merge target captured at ``wt create``).

    HATS-533: ``WorktreeManager._fast_forward_merge`` / ``_squash_merge``
    invoke ``git merge`` in the main-repo cwd without first checking out
    ``self._original_branch``. If main-repo HEAD moved between
    ``wt create`` and ``wt merge`` (manual ``git checkout``, a peer agent
    operating directly in main repo without a linked worktree, an IDE
    branch-switch, etc.), the merge silently lands on whatever branch is
    currently checked out — same silent-wrong-branch-merge class as
    HATS-486. This guard refuses BEFORE any mutation. The recipe is owned
    by CLI handlers (``cli/worktree.py wt_merge``, ``cli/task.py
    task_transition``) — exception body is facts-only (HATS-509 contract).

    Recovery: ``git checkout <expected>`` in the main repo, then re-run.
    The original branch is preserved unchanged either way.

    Live incident motivating the guard: HATS-509 session (2026-05-26).
    Worktree created from master (HATS-518 passed). Between create and
    merge a peer agent committed directly on ``task/hats-514`` in main
    repo, leaving HEAD wandered. ``task transition done`` merged
    ``task/hats-509`` into ``task/hats-514`` instead of master. Recovered
    via ``git cherry-pick``.

    **``--force`` does NOT bypass this guard.** Symmetric to
    :class:`WorktreeBaseBranchError`: ``--force`` overrides the FSM
    arrow, not safety contracts. Operator checks out the right branch
    explicitly.
    """

    def __init__(self, current: str, expected: str) -> None:
        self.current = current
        self.expected = expected
        super().__init__(
            f"main repo HEAD is on '{current}', not '{expected}' — the "
            f"worktree was created from '{expected}' and `wt merge` would "
            f"otherwise land on the current branch instead of the merge "
            f"target."
        )


class WorktreeMainRepoMidMergeError(Exception):
    """Raised when ``wt merge`` runs while the main repo already has an
    unfinished merge in progress (``MERGE_HEAD`` present).

    HATS-587 / F4: ``_fast_forward_merge`` / ``_squash_merge`` invoke
    ``git merge`` in the main-repo cwd. If a *foreign* merge is already
    underway there (a conflicting peer merge left mid-resolution, an IDE
    "merge branch" the operator never finished, a prior aborted run), git
    refuses with ``exit 128`` and a raw ``CalledProcessError`` reaches the
    CLI as an unhandled traceback. This guard refuses BEFORE any mutation
    so the worktree and branch are left untouched and the operator gets an
    actionable hint instead of a stack trace.

    The recipe (``git merge --abort`` / resolve, then re-run) is owned by
    the CLI handlers — the exception body is facts-only (HATS-509 contract).

    Live incident motivating the guard: 2026-05-28 session — ``wt merge``
    ran while the main repo was mid-merge of an unrelated HATS-570 branch.
    """

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        super().__init__(
            f"main repo at '{project_dir}' is mid-merge (MERGE_HEAD present) "
            f"— refusing to start another merge on top of an unfinished one."
        )


#: Branch names considered "canonical bases" for worktree creation. The
#: first one that actually exists in the repo is the comparison target.
#: Hardcoded by design (HATS-518): 99% repo coverage, no new config
#: primitive until a second use case appears (YAGNI / design-minimalism).
CANONICAL_BASE_BRANCHES: tuple[str, ...] = ("master", "main")


def assert_head_is_canonical_base(project_dir: Path) -> None:
    """Refuse if main-repo HEAD is not on a canonical base branch.

    No-op when:
      * not a git repo (no ``.git`` dir — caller has its own short-circuit);
      * HEAD is detached (no branch name to compare against);
      * none of :data:`CANONICAL_BASE_BRANCHES` exist in this repo
        (exotic naming — no canon to compare against, pass through rather
        than block valid workflows).

    :raises WorktreeBaseBranchError: HEAD is on a named branch, at least
        one canonical base exists, and HEAD is not one of them.
    """
    if not (project_dir / ".git").exists():
        return

    try:
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # Can't introspect — fall through to existing behavior.

    # Detached HEAD: `rev-parse --abbrev-ref HEAD` returns the literal
    # string "HEAD". No branch name to compare; skip the guard rather
    # than block (operator on a SHA knows what they're doing).
    if head == "HEAD":
        return

    existing_canonical: list[str] = []
    for name in CANONICAL_BASE_BRANCHES:
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", name],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
            )
            existing_canonical.append(name)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    if not existing_canonical:
        return  # No canon in this repo — nothing to compare against.

    if head in existing_canonical:
        return

    raise WorktreeBaseBranchError(current=head, canonical=existing_canonical)


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

            # HATS-517 — branch-exists classifier. Three sub-cases share the
            # symptom "git worktree add -b … fails with 'already exists'":
            #
            #   Case C  branch is already a LINKED worktree, state JSON is
            #           missing (manual delete, backup restore). Adopt the
            #           existing linked path and persist a fresh state.
            #   Case B  branch is checked out in the MAIN worktree
            #           (project_dir). Adopting project_dir would silently
            #           disable auto-merge in _teardown_worktree — FSM
            #           contract divergence. Refuse with actionable hint.
            #   Case A  branch exists but no worktree owns it (e.g. user ran
            #           `git branch …` ahead of time). Attach to a new
            #           linked worktree via `git worktree add <path> <branch>`
            #           (positional, no -b). Normal lifecycle proceeds.
            #
            # Order matters: Case C / Case B are detected via
            # `git worktree list`; Case A is the residual (branch exists but
            # is not in `list_worktrees`). The classifier sits inside L1, so
            # HATS-479 mutex invariants are preserved.
            existing_wt_path = (
                self._find_linked_worktree_for_branch(
                    self.project_dir, self.branch_name
                )
                if branch_existed_before
                else None
            )
            attach_existing_branch = False
            if existing_wt_path is not None:
                # Case B: same branch is checked out in the main worktree.
                if existing_wt_path.resolve() == self.project_dir.resolve():
                    raise WorktreeCreateError(
                        f"Cannot create worktree on '{self.branch_name}': "
                        f"branch is currently checked out in the main "
                        f"worktree ({self.project_dir}).\n"
                        f"  The execute transition needs an isolated "
                        f"worktree, but adopting the main project tree "
                        f"would silently disable auto-merge on `task "
                        f"transition done`.\n"
                        f"  Resolve by either:\n"
                        f"    - switch off the branch: "
                        f"`git switch <other-branch>` (e.g. master), or\n"
                        f"    - if the work is already shipped on main: "
                        f"`ai-hats task close <ID> --resolution \"shipped on "
                        f"main\"`."
                    )
                # Case C subtlety: linked-worktree admin entry exists but
                # the directory was rmtree'd without `git worktree remove`.
                # We refuse rather than auto-pruning (HATS-488 / R-04
                # explicitly dropped auto-prune to avoid racing with
                # concurrent wt create).
                if not existing_wt_path.exists():
                    raise WorktreeCreateError(
                        f"Cannot create worktree on '{self.branch_name}': "
                        f"git tracks a linked worktree at "
                        f"{existing_wt_path}, but the directory is gone.\n"
                        f"  Clean the orphan admin entry manually: "
                        f"`git worktree prune` (review with "
                        f"`git worktree list` first)."
                    )
                # Case C happy path: adopt the existing linked worktree.
                self.worktree_path = existing_wt_path
                self.save_state()
                logger.info(
                    "Adopted existing linked worktree %s for branch %s "
                    "(state JSON re-created — HATS-517 Case C)",
                    existing_wt_path, self.branch_name,
                )
                return self.worktree_path
            if branch_existed_before:
                # Case A: branch exists, no worktree owns it. Attach.
                attach_existing_branch = True
                logger.info(
                    "Branch %s already exists; attaching to a new linked "
                    "worktree (HATS-517 Case A)", self.branch_name,
                )

            prefix = self.branch_name.replace("/", "-")
            tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-")
            self.worktree_path = Path(tmpdir)

            try:
                _retry_worktree_add(
                    self._git, self.branch_name, self.worktree_path,
                    create_branch=not attach_existing_branch,
                )
            except subprocess.CalledProcessError as exc:
                # L4: cleanup leaked tempdir + (only-our) branch.
                shutil.rmtree(self.worktree_path, ignore_errors=True)  # safe-delete: ok L4 cleanup of leaked mkdtemp on create failure
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

            # HATS-533: refuse if main-repo HEAD is no longer on the merge
            # target captured at create time. _fast_forward_merge /
            # _squash_merge run `git merge` in main-repo cwd, so a HEAD that
            # has wandered (manual checkout, peer agent operating directly
            # in main repo, IDE branch-switch) would silently merge into the
            # current branch — same wrong-branch-merge class as HATS-486.
            #
            # Ordering invariants (do not flip without re-thinking):
            #   1. AFTER `_acquire_lifecycle_lock` + the worktree-exists
            #      peer no-op (line ~1178) — a parallel discard that has
            #      already torn the dir down MUST short-circuit cleanly,
            #      regardless of HEAD position. A spurious mismatch
            #      refusal on top of a peer's completed teardown would
            #      surface as a confusing user-visible error for what is
            #      really a no-op.
            #   2. BEFORE `_check_clean`, `_check_drift`, the
            #      OriginalBranchMissing / `_branch_exists` check, and
            #      the merge mechanics themselves. With HEAD wrong, all
            #      of those answer the wrong question — drift = "did the
            #      base move?", clean = "is the wt tree dirty against
            #      its branch?". Refusing here keeps the user-visible
            #      message focused on the actionable root cause.
            #
            # Skip for legacy states where _original_branch is None
            # (symmetric with the OriginalBranchMissing guard below).
            if self._original_branch is not None:
                head = self._git(
                    "rev-parse", "--abbrev-ref", "HEAD"
                ).stdout.strip()
                if head != self._original_branch:
                    raise WorktreeBaseBranchMismatchError(
                        current=head, expected=self._original_branch
                    )

            # HATS-587 / F4: refuse if the main repo already has an
            # unfinished merge in progress. _fast_forward_merge /
            # _squash_merge run `git merge` in the main-repo cwd; on top of
            # a pre-existing MERGE_HEAD git exits 128 with a raw
            # CalledProcessError that would surface as an unhandled
            # traceback. Refuse here — BEFORE _check_clean / _check_drift /
            # the OriginalBranchMissing teardown and the merge mechanics —
            # so the worktree and branch are left fully untouched and the
            # operator gets an actionable hint (CLI owns the recipe). Place
            # alongside the HEAD-mismatch guard: both are main-repo
            # preconditions that must short-circuit before any mutation.
            if self._main_repo_mid_merge():
                raise WorktreeMainRepoMidMergeError(self.project_dir)

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
                # HATS-587 / F5: a failed merge (conflict, mid-resolution
                # git error) must leave BOTH the worktree dir and the
                # branch intact so the operator can resolve and re-run.
                # Pre-587 this block tore the worktree down and cleared
                # state, leaving an orphaned branch with no worktree —
                # recovery then required a manual `git merge --no-ff`.
                # Teardown happens ONLY on the success path below.
                logger.warning(
                    "Merge of %s failed; worktree and branch left intact for retry",
                    self.branch_name,
                    exc_info=True,
                )
                raise
            self._remove_worktree()
            self._delete_branch()
            self._clear_state()
            # Match discard() / cleanup() teardown contract: a successful
            # merge invalidates self for any further lifecycle ops.
            self.worktree_path = None

    def discard(self, *, force: bool = False, force_remove: bool = False) -> None:
        """Remove worktree and branch without merging.

        Raises WorktreeDirtyError if the worktree has uncommitted changes
        unless force=True (HATS-062).

        Raises WorktreeRemoveError if ``git worktree remove --force``
        fails AND the directory is still on disk AND ``force_remove``
        was not passed (HATS-488 / B-03 — pre-488 path silently nuked
        data).

        HATS-480: holds the per-wt-branch lifecycle lock through the
        entire body. Parallel ``discard()`` or ``merge()`` on the same
        branch serializes; the second one observes the worktree dir
        already gone and no-ops idempotently.

        :param force: bypass the uncommitted-changes check (HATS-062).
        :param force_remove: bypass the data-preservation guard around
            the rmtree fallback (HATS-488 / B-03). Independent of
            ``force`` — uncommitted-changes check and on-disk cleanup
            are separate concerns.
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
            self._remove_worktree(force_rmtree=force_remove)
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

        HATS-482: ``state.py`` lowercases ``task.id`` when constructing the
        branch (``f"task/{task.id.lower()}"``). With ``_state_key`` no
        longer lowercasing post-482, we must mirror that convention here
        or the lookup key will not match the saved file (e.g. ``HATS-086``
        → key ``task-HATS-086``, while ``save_state`` wrote ``task-hats-086``).
        """
        key = _state_key(f"task/{task_id.lower()}")
        return cls._load_by_key(project_dir, key)

    @classmethod
    def load_for_branch(cls, project_dir: Path, branch: str) -> WorktreeManager | None:
        """Load worktree state by branch name."""
        key = _state_key(branch)
        return cls._load_by_key(project_dir, key)

    @classmethod
    def branch_exists(cls, project_dir: Path, branch: str) -> bool:
        """Check whether ``branch`` exists as a local ref in ``project_dir``.

        Probe-only — does NOT touch worktree state. Used by
        ``state.py:_teardown_worktree`` to distinguish:

        * "no worktree, no branch" → legitimate admin no-op (return
          silently from teardown).
        * "no worktree state, but branch still exists" → previous
          merge failure orphaned the branch; teardown must fail loud
          to prevent a silent DONE transition (HATS-541).

        Uses ``git branch --list <branch>`` rather than ``git rev-parse``
        because we only care about local-ref existence; an upstream-only
        ref shouldn't satisfy the "branch is preserved" condition.
        Returns ``False`` if ``project_dir`` isn't a git repo.
        """
        try:
            result = subprocess.run(
                ["git", "branch", "--list", branch],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return False
        if result.returncode != 0:
            return False
        # `git branch --list` exits 0 even when no match — empty stdout
        # is the "branch absent" signal.
        return bool(result.stdout.strip())

    @staticmethod
    def _migrate_legacy_lowercase_state(state_path: Path, key: str) -> None:
        """One-shot rename of pre-HATS-482 lowercased state file (B-07).

        Pre-482 ``_state_key`` lowercased its output, so `Task/HATS-X`'s
        state lived at `task-hats-x.json`. Post-482 the same branch
        resolves to key `task-HATS-X`. If we're looking up the new key but
        only the old file exists, migrate it in place under the state
        lock.

        No-ops when:
          * the primary key file already exists, OR
          * the legacy lowercase variant doesn't exist, OR
          * primary key is already all-lowercase (no migration possible).

        Concurrency: both source and target acquire ``_acquire`` locks
        before mutation; second concurrent caller observes the rename
        already done and skips.
        """
        if state_path.exists():
            return
        lower_key = key.lower()
        if lower_key == key:
            return
        legacy_path = state_path.with_name(f"{lower_key}.json")
        if not legacy_path.exists():
            return
        # Lock-order: legacy (outer) → target (inner). Matches
        # _migrate_singleton's ordering convention (HATS-121 / R-07).
        with _acquire(legacy_path):
            if not legacy_path.exists() or state_path.exists():
                return
            with _acquire(state_path):
                try:
                    legacy_path.rename(state_path)
                except OSError as exc:
                    # rename failed for an unexpected reason (perm, FS race)
                    # — leave legacy file in place and let caller treat key
                    # as missing. Loud log so this isn't silent.
                    logger.warning(
                        "Failed to migrate legacy state %s → %s: %s",
                        legacy_path, state_path, exc,
                    )
                    return
                logger.info(
                    "Migrated legacy lowercase worktree state %s → %s "
                    "(HATS-482 case-preserving keys)",
                    legacy_path.name, state_path.name,
                )

    @classmethod
    def _load_by_key(cls, project_dir: Path, key: str) -> WorktreeManager | None:
        state_path = worktrees_dir(project_dir) / f"{key}.json"
        # HATS-482 (B-07): one-shot migration of legacy lowercase state file.
        # Pre-482 `_state_key` lowercased its output; an upgrade may leave
        # `task-hats-x.json` on disk while the caller now queries with
        # case-preserving key `task-HATS-X`. If primary key missing AND a
        # lowercase variant exists AND it's not the same path, rename.
        cls._migrate_legacy_lowercase_state(state_path, key)
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
        """Load all active worktree states. Prunes stale entries.

        HATS-482 (R-05): best-effort under concurrent ``_clear_state``.
        We glob ``*.json`` once and then call :meth:`_load_by_key` per
        entry; between scan and load, a peer's ``discard()`` /
        ``cleanup()`` may unlink a file. ``_load_by_key`` returns ``None``
        on ``FileNotFoundError`` (no exception leaks), so the result list
        omits the racing entry — a transient lie, not corruption. Snapshot
        semantics would require holding a directory-wide lock for the
        duration of the iteration, which is heavier than the cost of an
        occasional missing row in ``wt list``.
        """
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

        HATS-482 (R-07): nested-lock ordering is **outer = legacy
        singleton, inner = new per-key path** — the only place where
        per-key lock is acquired while another worktree state lock is
        already held. Any future extension of singleton-path logic
        (additional read-then-write under the outer lock) MUST preserve
        this ordering: outer = singleton, inner = per-key. Inverting the
        order in another code path (per-key outer, then singleton inner)
        would re-introduce the deadlock window this comment prevents.
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

        HATS-490: single ``git rev-parse`` invocation with both flags —
        git emits one line per ref-info flag. Closes the race window
        between two separate subprocesses (e.g. ``.git`` rename in the
        middle would have given mismatched paths from the two calls)
        and saves a fork.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute",
                 "--git-dir", "--git-common-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 2:
            return False
        git_dir, common_dir = lines
        return Path(git_dir).resolve() != Path(common_dir).resolve()

    @classmethod
    def _find_linked_worktree_for_branch(
        cls, project_dir: Path, branch: str
    ) -> Path | None:
        """Return the on-disk worktree path that currently has ``branch``
        checked out, or ``None`` if no git worktree owns it.

        HATS-517 Case C helper: when the ai-hats state JSON is missing but a
        linked worktree for the branch already exists (manual JSON delete,
        backup restore, machine migration), we adopt the existing path
        instead of failing with "branch already exists".

        Returns the path for both the **main** worktree (`project_dir`)
        and **linked** worktrees — callers MUST distinguish the two when
        deciding what to do (Case B refuses main; Case C adopts linked).
        """
        for entry in cls.list_worktrees(project_dir):
            if entry.get("branch") == branch and "path" in entry:
                return Path(entry["path"])
        return None

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

    def _main_repo_mid_merge(self) -> bool:
        """True iff the main repo has an unfinished merge in progress.

        HATS-587 / F4: probes ``MERGE_HEAD`` in the main-repo cwd (where
        ``_fast_forward_merge`` / ``_squash_merge`` run ``git merge``).
        ``git rev-parse --verify --quiet MERGE_HEAD`` exits 0 only while a
        merge is mid-resolution. Any git error (not a repo, exotic state)
        falls back to ``False`` — the guard is a courtesy refusal, not a
        correctness gate, so "can't tell" must not block a valid merge.
        """
        try:
            self._git("rev-parse", "--verify", "--quiet", "MERGE_HEAD")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        """True iff ``maybe_ancestor`` is an ancestor of ``descendant`` per git.

        Wraps ``git merge-base --is-ancestor`` — exit 0 = is ancestor,
        exit 1 = not ancestor, exit ≥2 = git error (broken ref, missing
        binary, etc.). Falls back to ``False`` on any error so callers
        treat "can't tell" the same as "not ancestor" (safer default for
        drift-style checks: if we can't prove the ref relationship, do
        NOT silently suppress the drift signal).

        HATS-487: used by :meth:`_check_drift` to distinguish real remote
        drift (remote has commits local doesn't) from unpushed local work
        (local has commits remote doesn't — remote IS ancestor of local).
        """
        try:
            self._git("merge-base", "--is-ancestor", maybe_ancestor, descendant)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
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
            local branch has not pulled — i.e. ``origin/<base>`` is NOT
            an ancestor of local. ``current_remote != current_local`` is
            insufficient: that condition also fires for normal unpushed
            local work (HATS-487 false-positive).

        ``git fetch origin <base>`` runs first to surface remote drift.
        Failures are logged at WARNING (HATS-489 / B-04): merge follows
        immediately, so a swallowed fetch error can hide a real
        remote-side push that we'd otherwise catch. Not raise — offline
        / no-remote setups must still be able to merge.

        Skips silently when the saved ``base_sha_at_create`` is missing
        (legacy state file from before HATS-457).
        """
        if self._base_sha_at_create is None or self._original_branch is None:
            return

        # HATS-489 / B-04: fetch failure escalated DEBUG → WARNING.
        # HATS-489 / B-05: FileNotFoundError (git binary missing) caught
        # consistently with CalledProcessError (mirrors
        # is_inside_linked_worktree / list_worktrees).
        try:
            self._git("fetch", "origin", self._original_branch)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = (getattr(exc, "stderr", "") or "").strip()
            tail = stderr.splitlines()[-1] if stderr else "<no stderr>"
            logger.warning(
                "Drift check: fetch origin %s failed (%s); proceeding with "
                "local-only check — remote-side drift will NOT be detected "
                "this run",
                self._original_branch, tail,
            )

        try:
            current_local = self._git(
                "rev-parse", self._original_branch
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Can't read the original branch SHA — let the missing-branch
            # path in merge() handle it.
            return

        current_remote: str | None
        try:
            current_remote = self._git(
                "rev-parse", "--verify", "--quiet", f"origin/{self._original_branch}"
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            current_remote = None

        local_drifted = current_local != self._base_sha_at_create
        # HATS-487: real remote drift means remote has commits NOT in
        # local — equivalent to "remote is NOT an ancestor of local".
        # Unpushed local work (local is ancestor of remote? — no, the
        # other way: remote IS ancestor of local) was previously
        # false-positiv'd as "remote drift, 0 commits ahead" with a
        # nonsense diff list.
        remote_drifted = (
            current_remote is not None
            and current_remote != current_local
            and not self._is_ancestor(current_remote, current_local)
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
        # HATS-509: the exception body carries facts only (drift summary).
        # The user-facing "re-run with `ai-hats wt merge --accept-drift`"
        # recipe is added by CLI handlers (cli/worktree.py wt_merge,
        # cli/task.py task_transition) so each command names the correct
        # surface — historically the literal trailer leaked into
        # `task transition done`, where the flag does NOT exist.
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
                project_dir=self.project_dir,  # HATS-486 stale-lock probe
            )
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "commit", "-m", f"feat(agent): {self.branch_name}",
                project_dir=self.project_dir,  # HATS-486 stale-lock probe
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
                project_dir=self.project_dir,  # HATS-486 stale-lock probe
            )
        logger.info("Merged %s into %s", self.branch_name, self._original_branch)

    def _remove_worktree(self, *, force_rmtree: bool = False) -> None:
        """Remove the worktree from git and filesystem.

        HATS-488 (B-03 + R-04) — pre-488 fallback path silently
        ``shutil.rmtree`` (``ignore_errors=True``) + auto-``git worktree
        prune``. Two problems:

        * **B-03**: rmtree ignoring errors nuked uncommitted work that
          ``git worktree remove --force`` had refused to delete (e.g.
          held-open files) → silent data loss.
        * **R-04**: ``git worktree prune`` walks all
          ``.git/worktrees/`` admin entries; a concurrent ``wt create``
          peer that hasn't yet materialized its target dir can have
          its admin entry unlinked.

        Post-488 contract:

        * Default ``force_rmtree=False``: if ``git worktree remove
          --force`` fails AND the dir is still on disk, raise
          :class:`WorktreeRemoveError`. Operator's call whether to
          ``rm -rf`` manually or re-invoke ``wt discard --force-remove``.
        * Opt-in ``force_rmtree=True`` (CLI flag ``--force-remove``):
          best-effort ``shutil.rmtree`` is permitted; logs at WARNING.
          A residual dir after rmtree still raises (broken symlinks,
          perm).
        * Auto-prune is gone unconditionally; orphan admin entries are
          surfaced via ``wt list`` and cleaned by manual ``git worktree
          prune``.
        """
        if self.worktree_path is None:
            return
        try:
            self._git("worktree", "remove", str(self.worktree_path), "--force")
            return
        except subprocess.CalledProcessError as exc:
            if not self.worktree_path.exists():
                # Dir already gone (concurrent external removal / cleanup
                # by a peer post-HATS-480 lifecycle lock release). git's
                # bookkeeping might be stale; do NOT auto-prune (R-04).
                logger.info(
                    "Worktree dir already absent (%s); git removal failed "
                    "harmlessly",
                    self.worktree_path,
                )
                return
            stderr = (exc.stderr or "").strip()
            tail = stderr.splitlines()[-1] if stderr else "<no stderr>"
            if not force_rmtree:
                raise WorktreeRemoveError(self.worktree_path, tail) from exc
            # Opt-in path: --force-remove explicit consent.
            logger.warning(
                "force-removing worktree dir after git failure: %s (git: %s)",
                self.worktree_path, tail,
            )
            try:
                shutil.rmtree(self.worktree_path)  # safe-delete: ok force-remove opt-in (HATS-488)
            except OSError as rmtree_exc:
                # Even force-rmtree couldn't clean (broken symlink, perm,
                # ENOTEMPTY race). Surface; operator may need elevated
                # access or external cleanup.
                raise WorktreeRemoveError(
                    self.worktree_path,
                    f"rmtree: {rmtree_exc}; git: {tail}",
                ) from rmtree_exc

    # HATS-482 (B-02): stderr substrings → classified causes for
    # `_delete_branch` failures. Matched case-insensitively.
    # "not fully merged" — git's exact phrasing for unmerged-branch refusal.
    # "checkout" / "used by worktree" — branch checked out elsewhere
    #   (covers both pre-2.21 and post-2.21 git phrasings).
    # "cannot lock ref" / "unable to lock" — ref-lock contention; usually
    #   transient but worth surfacing if it persists past the L1' / L3'
    #   retries (HATS-481).
    _DELETE_BRANCH_REASONS = (
        ("not fully merged", "not_fully_merged"),
        ("used by worktree", "checked_out"),
        ("checkout", "checked_out"),
        ("cannot lock ref", "locked"),
        ("unable to lock", "locked"),
    )

    def _classify_delete_branch_error(
        self, stderr: str
    ) -> tuple[str, str] | None:
        """Return ``(reason, stderr_tail)`` for known causes, None otherwise."""
        s = (stderr or "").lower()
        tail = (stderr or "").strip().splitlines()[-1] if stderr.strip() else ""
        for needle, reason in self._DELETE_BRANCH_REASONS:
            if needle in s:
                return reason, tail
        return None

    def _delete_branch(self) -> None:
        """Delete the worktree branch.

        HATS-482 (B-02): pre-482 swallowed all errors at DEBUG, hiding
        "not fully merged" (data-loss WARNING) and "used by worktree"
        (operator-actionable). Classified causes now raise
        :class:`WorktreePartialCleanupError` so the CLI surfaces them with
        guidance and a non-zero exit code. Unknown stderr stays silent at
        DEBUG (regression-safe — pre-482 success path unchanged).
        """
        try:
            self._git("branch", "-D", self.branch_name)
        except subprocess.CalledProcessError as exc:
            classified = self._classify_delete_branch_error(exc.stderr or "")
            if classified is None:
                logger.debug(
                    "Could not delete branch %s (unclassified): %s",
                    self.branch_name,
                    (exc.stderr or "").strip().splitlines()[-1]
                    if (exc.stderr or "").strip() else "<no stderr>",
                )
                return
            reason, tail = classified
            logger.warning(
                "Branch '%s' preserved (%s): %s",
                self.branch_name, reason, tail,
            )
            raise WorktreePartialCleanupError(
                self.branch_name, reason, tail,
            ) from exc
