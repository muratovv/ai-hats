"""Lock & retry concurrency primitives for worktree isolation.

Extracted from ``worktree.py`` in the HATS-715 wt-core split. Owns the per-key
state-JSON lock, the create / base / lifecycle locks, and the jittered-backoff
git retries.

Architectural model, layer rationale, and the operator-guard / teardown /
stale-lock hardening: ``docs/adr/0006-worktree-concurrency-layered-defense.md``
— this docstring is its canonical in-code mirror.

Lock-ordering hierarchy — always acquired outer -> inner, no inversion, so no
deadlock is reachable by construction:

1. ``<state>.json.lifecycle.lock``           — HATS-480 (per wt branch)
2. ``<state_dir>/.base-<base>.lock``         — HATS-481 (per base ref)
3. ``<state_dir>/.git-worktree-create.lock`` — HATS-479 (repo-wide, create-only)
4. ``<state>.json.lock``                     — HATS-121 (per state JSON, I/O only)

The lock directory ``<state_dir>`` **must reside on a local filesystem** —
``filelock.FileLock`` (``fcntl`` advisory) is unreliable on NFS / SMB.
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import filelock

from ..utils.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = 10.0  # seconds — see module docstring

# HATS-479 — create-time concurrency (see module docstring "Create-time concurrency")
CREATE_LOCK_TIMEOUT = 10.0  # L1: repo-scoped mutex acquisition
GIT_RETRY_MAX = 5  # L3: 1 initial + 4 retries
GIT_RETRY_BASE_DELAY = 0.05  # 50 ms, exponential up to GIT_RETRY_MAX_DELAY
GIT_RETRY_MAX_DELAY = 0.8  # cap per-attempt delay so 5 retries finish < 4 s
CREATE_LOCK_CONTENTION_WARN = 1.0  # log at WARNING if acquisition took longer

# HATS-481 — base-branch merge serialization (see module docstring "Merge-time concurrency")
BASE_LOCK_TIMEOUT = 15.0  # L1' acquisition cap — covers a ~20-way pile-up
MERGE_RETRY_MAX = 8  # AWS canonical at our scale
MERGE_RETRY_BASE_DELAY = 0.1  # 100 ms — matches git's core.*LockTimeout default
MERGE_RETRY_MAX_DELAY = 5.0  # 5 s cap; longer wait = real work, not contention
REF_LOCK_TIMEOUT_MS = 5000  # passed to git as core.filesRefLockTimeout — covers
# ref-lock contention for free (no index.lock equivalent)

# HATS-480 — per-branch lifecycle serialization (see module docstring "Lifecycle concurrency")
LIFECYCLE_LOCK_TIMEOUT = 60.0  # covers fetch + merge + remove + branch -D end-to-end

# HATS-711 — wall-clock cap on the pre-merge network `fetch` in _check_drift.
# Held inside the lifecycle lock, so an unbounded fetch (dead VPN / DNS
# blackhole; TCP stalls sit for minutes) would wedge merge() and make peers
# time out at LIFECYCLE_LOCK_TIMEOUT with a misleading "concurrent
# wt merge/discard" error. 30s sits comfortably inside the 60s lifecycle
# budget (fetch + sub-second local merge/remove/branch-D) and mirrors the
# network-fetch timeout already used in update_check/checker.py.
FETCH_TIMEOUT = 30.0

# HATS-486 — stale .git/index.lock observability (see module docstring
# "Stale-lock observability"). Threshold above which the lock is treated
# as evidence of a crashed git process (warn-only — no auto-delete in v1).
STALE_INDEX_LOCK_THRESHOLD_S = 60.0


class WorktreeLockError(Exception):
    """Raised when acquiring a worktree state lock times out (HATS-121)."""


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
    """Write JSON atomically via the canonical helper (HATS-716)."""
    atomic_write_text(path, json.dumps(data, indent=2))


def _create_lock_path(state_dir: Path) -> Path:
    """Repo-scoped create-lock file path (HATS-479 L1).

    ``state_dir`` is the injected path-base (ADR-0013 D4); ai-hats passes its
    ``worktrees_dir(project_dir)`` convention, a bare core a project-local dir.
    """
    return state_dir / ".git-worktree-create.lock"


@contextmanager
def _acquire_create_lock(state_dir: Path) -> Iterator[None]:
    """Hold the repo-scoped create-mutex for the wt-create critical section.

    HATS-479 L1. See module docstring "Create-time concurrency".

    Serializes ai-hats vs. ai-hats writes to ``.git/config``, ``.git/refs``,
    ``.git/worktrees/``. Does NOT protect against external git processes
    (IDE, manual ``git commit``) — :func:`_retry_worktree_add` covers that.
    """
    lock_path = _create_lock_path(state_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=CREATE_LOCK_TIMEOUT)
    t0 = time.monotonic()
    try:
        with lock:
            waited = time.monotonic() - t0
            if waited > CREATE_LOCK_CONTENTION_WARN:
                logger.warning("wt create lock acquired after %.2fs (contention)", waited)
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


def _base_lock_path(state_dir: Path, base_branch: str) -> Path:
    """Sibling lock file for a base ref (HATS-481 L1').

    ``state_dir`` is the injected path-base (ADR-0013 D4) — see
    :func:`_create_lock_path`.
    """
    return state_dir / f".base-{_base_lock_key(base_branch)}.lock"


@contextmanager
def _acquire_base_branch_lock(
    state_dir: Path, base_branch: str, *, timeout: float = BASE_LOCK_TIMEOUT
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
    lock_path = _base_lock_path(state_dir, base_branch)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = filelock.FileLock(str(lock_path), timeout=timeout)
    t0 = time.monotonic()
    try:
        with lock:
            waited = time.monotonic() - t0
            if waited > CREATE_LOCK_CONTENTION_WARN:
                logger.warning(
                    "base-branch merge lock acquired after %.2fs (contention on '%s')",
                    waited,
                    base_branch,
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
                    waited,
                    state_path.name,
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
                attempt,
                GIT_RETRY_MAX,
                (exc.stderr or "").strip().splitlines()[-1] if exc.stderr else "<no stderr>",
            )
            sleep(delay + jitter)
            delay = min(delay * 2, GIT_RETRY_MAX_DELAY)
    assert last_exc is not None
    raise last_exc


def _format_git_create_error(exc: subprocess.CalledProcessError, branch: str) -> str:
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
    return f"git worktree add failed for branch '{branch}'.\n  git: {head}"


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
                        age,
                        STALE_INDEX_LOCK_THRESHOLD_S,
                        lock_path,
                    )
            last_exc = exc
            if attempt == MERGE_RETRY_MAX:
                break
            ceiling = min(MERGE_RETRY_MAX_DELAY, delay)
            wait = random.uniform(0, ceiling)
            cmd_label = git_args[0] if git_args else "<no-cmd>"
            logger.info(
                "git %s transient lock contention (attempt %d/%d), waiting %.2fs: %s",
                cmd_label,
                attempt,
                MERGE_RETRY_MAX,
                wait,
                (exc.stderr or "").strip().splitlines()[-1] if exc.stderr else "<no stderr>",
            )
            sleep(wait)
            delay = min(delay * 2, MERGE_RETRY_MAX_DELAY)
    assert last_exc is not None
    raise last_exc
