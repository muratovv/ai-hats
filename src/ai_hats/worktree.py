"""Git worktree isolation for sub-agent execution (HATS-004).

:class:`WorktreeManager` creates and manages linked git worktrees and their
create / merge / discard lifecycle. The lock & retry concurrency infrastructure
that serializes those operations — and the full lock-ordering model — lives in
:mod:`ai_hats.worktree_locks` (extracted in HATS-715).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any


from .models import WT_TEARDOWN_EVENTS
from .paths import managed_wt_hook_filename, worktrees_dir, wt_hooks_dir
from .worktree_hooks import run_worktree_hook

from .worktree_locks import (  # noqa: F401  -- re-export preserves the import surface (HATS-715)
    BASE_LOCK_TIMEOUT,
    CREATE_LOCK_CONTENTION_WARN,
    CREATE_LOCK_TIMEOUT,
    FETCH_TIMEOUT,
    GIT_RETRY_BASE_DELAY,
    GIT_RETRY_MAX,
    GIT_RETRY_MAX_DELAY,
    LIFECYCLE_LOCK_TIMEOUT,
    LOCK_TIMEOUT,
    MERGE_RETRY_BASE_DELAY,
    MERGE_RETRY_MAX,
    MERGE_RETRY_MAX_DELAY,
    REF_LOCK_TIMEOUT_MS,
    STALE_INDEX_LOCK_THRESHOLD_S,
    WorktreeLockError,
    _RETRIABLE_MERGE_STDERR_PATTERNS,
    _RETRIABLE_STDERR_PATTERNS,
    _acquire,
    _acquire_base_branch_lock,
    _acquire_create_lock,
    _acquire_lifecycle_lock,
    _atomic_write_json,
    _base_lock_key,
    _base_lock_path,
    _create_lock_path,
    _format_git_create_error,
    _is_retriable_git_error,
    _is_retriable_merge_error,
    _lifecycle_lock_path,
    _lock_path,
    _retry_git_merge,
    _retry_worktree_add,
    _state_key,
    _stale_index_lock_age,
)

logger = logging.getLogger(__name__)


class WorktreeDirtyError(Exception):
    """Raised when a destructive operation targets a worktree with uncommitted changes."""


class WorktreeHookError(Exception):
    """A ``wt_out`` lifecycle hook failed and the teardown is fail-closed (HATS-823).

    Raised by :meth:`WorktreeManager._run_wt_out_hooks` when a declared
    ``wt_out`` hook exits non-zero / times out / is missing / unrunnable and
    ``--skip-hooks`` was not given. The teardown aborts before
    ``_remove_worktree`` so the worktree + branch (and any unharvested
    gitignored data) are preserved (ADR-0012 D4). On the ``cleanup()`` path it
    is caught and logged — never raised over the agent's original error."""


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
        super().__init__(f"Branch '{branch_name}' could not be deleted ({reason}): {stderr_tail}")


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


class WorktreeStateIncompleteError(Exception):
    """State file present but ``original_branch`` is ``None`` (corrupt /
    hand-edited / pre-versioned JSON).

    HATS-714: every ``merge()`` guard is gated on ``_original_branch is not
    None``, so ``None`` would otherwise reach ``git rev-parse None`` → an
    opaque ``TypeError``. This is the typed refusal instead.
    """

    def __init__(self, branch_name: str) -> None:
        self.branch_name = branch_name
        super().__init__(
            f"Worktree state for '{branch_name}' lacks 'original_branch' "
            f"(corrupt or legacy state file). Recreate it via `ai-hats wt "
            f"create` adoption, or merge the branch manually."
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

    HATS-602: the guard is evaluated INSIDE the base-branch lock (via
    :meth:`WorktreeManager._refuse_if_mid_merge`, called from
    ``_fast_forward_merge`` / ``_squash_merge``), NOT in ``merge()`` before
    the lock. A concurrent peer ai-hats merge holds that lock across its
    ``git merge``, so the pre-602 placement saw the peer's *transient*
    ``MERGE_HEAD`` and spuriously refused (two parallel merges into the same
    base flaked). Inside the lock only a genuinely-stuck FOREIGN
    ``MERGE_HEAD`` remains. The raise still precedes any mutation.

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
        mgr = WorktreeManager.load_for_branch(project_dir, "feat/hats-004")
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
        # HATS-827: backstop — empty role yields the git-invalid branch
        # agent//<sid>; fail at construction, not deep in create().
        if not branch_name and not role_name:
            raise ValueError(
                "cannot build worktree branch: empty role segment — pass a role"
            )
        self.branch_name = branch_name or f"agent/{role_name}/{session_id}"
        self._is_git = False
        self._original_branch: str | None = None
        self._base_sha_at_create: str | None = None  # HATS-457
        # HATS-823: create-time carry {wt_in/wt_out: [{skill, script, on}]},
        # persisted to state and replayed at teardown (never recomposed).
        self._wt_hooks: dict[str, list[dict[str, Any]]] = {}
        # HATS-823: state predates wt-hooks (key absent, not empty {}) → WARN.
        self._wt_hooks_legacy = False

    def create(
        self,
        *,
        wt_hooks: dict[str, list[dict[str, Any]]] | None = None,
    ) -> Path:
        """Create an isolated worktree. Returns project_dir if not a git repo
        or if isolation_mode is NONE (no worktree, runs in project_dir).

        ``wt_hooks`` (HATS-823) are the resolved worktree lifecycle hooks the
        caller collected from composition (``collect_worktree_hooks`` →
        ``serialize_collected_hooks``). They are stored on the manager, run at
        ``wt_in`` time (after ``git worktree add``), and persisted by
        :meth:`save_state` so teardown runs the create-time set verbatim.

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
        self._wt_hooks = wt_hooks or {}
        if self.isolation_mode == IsolationMode.NONE:
            # No worktree → wt_in/wt_out never run (D7); worktree_path stays None.
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
            self._base_sha_at_create = self._git("rev-parse", self._original_branch).stdout.strip()
        except subprocess.CalledProcessError:
            self._base_sha_at_create = None

        # HATS-479 — L1 + L2 + L4. See module docstring "Create-time concurrency".
        with _acquire_create_lock(self.project_dir):
            # L2: re-check under the lock. Closes the TOCTOU window between a
            # caller's optional pre-check and our work.
            existing = WorktreeManager.load_for_branch(self.project_dir, self.branch_name)
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
                self._find_linked_worktree_for_branch(self.project_dir, self.branch_name)
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
                        f'`ai-hats task close <ID> --resolution "shipped on '
                        f'main"`.'
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
                    existing_wt_path,
                    self.branch_name,
                )
                return self.worktree_path
            if branch_existed_before:
                # Case A: branch exists, no worktree owns it. Attach.
                attach_existing_branch = True
                logger.info(
                    "Branch %s already exists; attaching to a new linked "
                    "worktree (HATS-517 Case A)",
                    self.branch_name,
                )

            prefix = self.branch_name.replace("/", "-")
            tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-")
            self.worktree_path = Path(tmpdir)

            try:
                _retry_worktree_add(
                    self._git,
                    self.branch_name,
                    self.worktree_path,
                    create_branch=not attach_existing_branch,
                )
            except subprocess.CalledProcessError as exc:
                # L4: cleanup leaked tempdir + (only-our) branch.
                shutil.rmtree(
                    self.worktree_path, ignore_errors=True
                )  # safe-delete: ok L4 cleanup of leaked mkdtemp on create failure
                self.worktree_path = None
                if not branch_existed_before:
                    try:
                        self._git("branch", "-D", self.branch_name)
                    except subprocess.CalledProcessError:
                        pass  # branch may not have been created — fine
                raise WorktreeCreateError(_format_git_create_error(exc, self.branch_name)) from exc
            # HATS-823: wt_in runs AFTER add (git refuses a non-empty dir).
            self._run_wt_in_hooks()
            logger.info(
                "Created worktree %s on branch %s",
                self.worktree_path,
                self.branch_name,
            )
            return self.worktree_path

    def merge(
        self,
        *,
        squash: bool = False,
        force: bool = False,
        accept_drift: bool = False,
        skip_hooks: bool = False,
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

        state_path = worktrees_dir(self.project_dir) / f"{_state_key(self.branch_name)}.json"
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

            # HATS-714: refuse before the gated guards below let None reach
            # `git rev-parse None`. See WorktreeStateIncompleteError.
            if self._original_branch is None:
                raise WorktreeStateIncompleteError(self.branch_name)

            # HATS-596: checkout-independent already-merged short-circuit.
            # The worktree-isolation contract: the task lives on its own
            # branch; the main checkout may legitimately be on ANY branch.
            # If the task-branch tip is already an ancestor of the recorded
            # base ref, the work is fully integrated — `git merge` would be a
            # no-op. (`_fast_forward_merge` / `_squash_merge` already
            # short-circuit `head_main == head_wt`, but that path is (a)
            # unreachable past the HEAD-mismatch guard below and (b) blind to
            # the `--no-ff` case where the base tip is a merge commit, not the
            # branch tip.)
            #
            # Because NO `git merge` runs here, the main-repo HEAD position is
            # irrelevant: refusing on a wandered HEAD (HATS-533) or a foreign
            # MERGE_HEAD (HATS-587 / F4) would be a FALSE refusal — the exact
            # bug HATS-596 fixes (work merged into master + pushed, but the
            # main checkout sat on a concurrent feature branch). So this MUST
            # precede both of those guards.
            #
            # `_check_clean` is still honored (force-bypassable, matching the
            # _check_clean contract) so uncommitted worktree edits are not
            # silently dropped. Drift is skipped: once the work is integrated,
            # base movement no longer matters. Uses the recorded refs only
            # (local base) — origin/<base> is out of scope (HATS-596 decision).
            if (
                self._original_branch is not None
                and self._branch_exists(self._original_branch)
                and self._is_ancestor(self.branch_name, self._original_branch)
            ):
                if not force:
                    self._check_clean()
                # HATS-823: short-circuit still destroys the dir → harvest first.
                self._run_wt_out_hooks("merge", skip_hooks=skip_hooks)
                self._remove_worktree()
                self._delete_branch()
                self._clear_state()
                self.worktree_path = None
                logger.info(
                    "Worktree '%s' already merged into '%s' — torn down "
                    "without re-merge (HATS-596)",
                    self.branch_name,
                    self._original_branch,
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
            #   3. AFTER the HATS-596 already-merged short-circuit above —
            #      when the work is already integrated no `git merge` runs,
            #      so a wandered HEAD is not a wrong-branch-merge risk and
            #      refusing here would be a false positive.
            #
            # Skip for legacy states where _original_branch is None
            # (symmetric with the OriginalBranchMissing guard below).
            #
            # Also skip when the recorded base branch no longer EXISTS: a
            # deleted base can never equal HEAD, so an un-gated comparison
            # always trips a misleading "base branch mismatch" and masks the
            # real diagnosis. That case is owned by the OriginalBranchMissing
            # guard below (it preserves the worktree branch for a manual
            # rebase). Gating here restores the pre-HATS-533 fall-through and
            # keeps `test_merge_raises_when_original_branch_deleted` green
            # (HATS-596: HATS-533 vs HATS-253 reconciliation).
            if self._original_branch is not None and self._branch_exists(self._original_branch):
                head = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
                if head != self._original_branch:
                    raise WorktreeBaseBranchMismatchError(
                        current=head, expected=self._original_branch
                    )

            # HATS-587 / F4 mid-merge guard moved (HATS-602): the
            # MERGE_HEAD check now runs INSIDE the base-branch lock, in
            # _fast_forward_merge / _squash_merge (via _refuse_if_mid_merge),
            # not here. A concurrent peer ai-hats merge holds the base lock
            # across its `git merge`, so checking outside the lock here
            # false-positived on the peer's *transient* MERGE_HEAD — two
            # parallel merges into the same base would spuriously refuse
            # (the HATS-602 flake). Inside the lock the peer's merge has
            # already completed, so only a genuinely-stuck FOREIGN MERGE_HEAD
            # trips the guard. It still refuses before any mutation (git
            # merge has not run yet), preserving the untouched-worktree
            # contract. The HEAD-mismatch guard above stays here: a peer
            # merge never moves the main-repo branch pointer, so it has no
            # concurrency false-positive.

            if not force:
                self._check_clean()
            if not accept_drift:
                self._check_drift()
            if self._original_branch and not self._branch_exists(self._original_branch):
                self._run_wt_out_hooks("merge", skip_hooks=skip_hooks)
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
            except WorktreeMainRepoMidMergeError:
                # HATS-602: this is a *precondition* refusal raised inside
                # the base lock (_refuse_if_mid_merge) — no `git merge` ran,
                # so it is NOT a merge failure. Propagate cleanly so the CLI
                # surfaces the actionable hint; skip the F5 "merge failed,
                # left intact for retry" + exc_info traceback below, which is
                # reserved for genuine merge failures (conflicts, git errors)
                # and would otherwise dump a misleading stack trace
                # (regression caught by test_wt_merge_mid_merge_refusal).
                raise
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
            # HATS-823: harvest before teardown. On failure the branch survives,
            # so a retry hits the HATS-596 short-circuit and re-runs the hook.
            self._run_wt_out_hooks("merge", skip_hooks=skip_hooks)
            self._remove_worktree()
            self._delete_branch()
            self._clear_state()
            # Match discard() / cleanup() teardown contract: a successful
            # merge invalidates self for any further lifecycle ops.
            self.worktree_path = None

    def discard(
        self,
        *,
        force: bool = False,
        force_remove: bool = False,
        skip_hooks: bool = False,
    ) -> None:
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

        state_path = worktrees_dir(self.project_dir) / f"{_state_key(self.branch_name)}.json"
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
            # HATS-823: discard != "accept data loss" — harvest fail-closed (D4).
            self._run_wt_out_hooks("discard", skip_hooks=skip_hooks)
            self._remove_worktree(force_rmtree=force_remove)
            self._delete_branch()
            self.worktree_path = None
            self._clear_state()

    def cleanup(self, *, force_discard: bool = False, skip_hooks: bool = False) -> None:
        """Clean up worktree. Merges changes based on isolation_mode.

        HATS-480: holds the per-wt-branch lifecycle lock through the
        entire body. A concurrent direct ``wt discard``/``wt merge`` on
        the same branch (issued by another agent / CLI while the
        context-manager is winding down) serializes against this call.
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = worktrees_dir(self.project_dir) / f"{_state_key(self.branch_name)}.json"
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

            # HATS-823: auto path — a wt_out failure must NOT raise over the
            # agent's original error (D4); preserve the dir and return.
            try:
                self._run_wt_out_hooks("cleanup", skip_hooks=skip_hooks)
            except WorktreeHookError as exc:
                logger.warning(
                    "wt_out hook failed during cleanup of '%s' — worktree "
                    "preserved; recover with `ai-hats wt discard %s --skip-hooks`: %s",
                    self.branch_name, self.branch_name, exc,
                )
                return

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
    # Worktree lifecycle hooks (HATS-823, ADR-0012)
    # ------------------------------------------------------------------

    def _wt_hook_log_dir(self) -> Path:
        return worktrees_dir(self.project_dir) / f"{_state_key(self.branch_name)}.logs"

    def _materialized_hook(self, row: dict[str, Any]) -> Path:
        """On-disk path of a hook script the assembler materialized."""
        return wt_hooks_dir(self.project_dir) / managed_wt_hook_filename(
            row["skill"], row["script"]
        )

    def _run_wt_in_hooks(self) -> None:
        """Run wt_in hooks after ``git worktree add`` (warn-and-continue).

        A create-time hook failure is friction, not data loss (ADR-0012 D3/D7):
        it is logged and skipped, never aborting worktree creation.
        """
        rows = self._wt_hooks.get("wt_in") or []
        if not rows or self.worktree_path is None:
            return
        log_dir = self._wt_hook_log_dir()
        for row in rows:
            script = self._materialized_hook(row)
            outcome = run_worktree_hook(
                script,
                event="wt_in",
                worktree_path=self.worktree_path,
                project_dir=self.project_dir,
                branch_name=self.branch_name,
                log_path=log_dir / f"wt_in-{script.name}.log",
            )
            if not outcome.ok:
                logger.warning(
                    "wt_in hook from skill '%s' failed — continuing "
                    "(create-time friction, not data loss): %s",
                    row.get("skill", "?"),
                    outcome.reason,
                )

    def _run_wt_out_hooks(self, event: str, *, skip_hooks: bool = False) -> None:
        """Run wt_out hooks bound to ``event`` before teardown removes the dir.

        Fail-closed (D4): any hook failure raises :class:`WorktreeHookError` and
        aborts the teardown, preserving the worktree. ``skip_hooks`` is the
        conscious escape; a no-op when no wt_out hooks are declared.
        """
        if self._wt_hooks_legacy:
            # Pre-upgrade worktree: can't know what it holds → warn, don't drop.
            logger.warning(
                "Worktree '%s' predates wt-hooks (no carry recorded at create) "
                "— gitignored data cannot be auto-harvested on %s; back it up "
                "manually if needed.",
                self.branch_name,
                event,
            )
        rows = [
            r
            for r in (self._wt_hooks.get("wt_out") or [])
            if event in (r.get("on") or WT_TEARDOWN_EVENTS)
        ]
        if self.worktree_path is None or not rows:
            return
        if skip_hooks:
            logger.warning(
                "wt_out hooks SKIPPED for '%s' on %s via --skip-hooks — "
                "unharvested gitignored data will be destroyed (%d hook(s))",
                self.branch_name,
                event,
                len(rows),
            )
            return
        log_dir = self._wt_hook_log_dir()
        for row in rows:
            script = self._materialized_hook(row)
            outcome = run_worktree_hook(
                script,
                event=event,
                worktree_path=self.worktree_path,
                project_dir=self.project_dir,
                branch_name=self.branch_name,
                log_path=log_dir / f"{event}-{script.name}.log",
            )
            if not outcome.ok:
                raise WorktreeHookError(
                    f"wt_out hook from skill '{row.get('skill', '?')}' failed on "
                    f"{event} ({outcome.reason}). Teardown aborted — worktree "
                    f"'{self.branch_name}' preserved. Fix the hook and retry, or "
                    f"force with --skip-hooks (accepts the data loss)."
                )

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
            "wt_hooks": self._wt_hooks,  # HATS-823: create-time hooks for teardown
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
        # Lock-order: legacy (outer) → target (inner) — never invert
        # (R-07 deadlock-avoidance for nested worktree-state locks).
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
                        legacy_path,
                        state_path,
                        exc,
                    )
                    return
                logger.info(
                    "Migrated legacy lowercase worktree state %s → %s "
                    "(HATS-482 case-preserving keys)",
                    legacy_path.name,
                    state_path.name,
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
        # HATS-823: absent key = pre-upgrade worktree (legacy WARN at teardown).
        mgr._wt_hooks = data.get("wt_hooks") or {}
        mgr._wt_hooks_legacy = "wt_hooks" not in data
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
                ["git", "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
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

    @staticmethod
    def main_worktree_root(path: Path) -> Path | None:
        """Return the main worktree's root iff `path` is in a linked worktree.

        Resolves through git's ``--git-common-dir``: a linked worktree's
        ``--git-dir`` points at ``<main>/.git/worktrees/<name>`` while its
        ``--git-common-dir`` points at the canonical ``<main>/.git``. When the
        two differ (linked worktree) the main root is ``common_dir.parent``;
        when they match (main worktree) or git can't tell, return ``None``.

        HATS-524: ``_project_dir`` uses this to hop from a linked worktree
        (whose checkout carries neither the gitignored ``.agent/`` nor the
        untracked ``ai-hats.yaml``) back to the main checkout, where the live
        tracker lives. Mirrors the single-``rev-parse`` invocation of
        :meth:`is_inside_linked_worktree` (HATS-490).

        Fail-safe: returns ``None`` on any subprocess error, non-git path, or
        unexpected output — callers fall back to current behaviour, never worse.
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 2:
            return None
        git_dir, common_dir = (Path(p).resolve() for p in lines)
        if git_dir == common_dir:
            return None  # main worktree — nothing to hop to
        return common_dir.parent

    @classmethod
    def _find_linked_worktree_for_branch(cls, project_dir: Path, branch: str) -> Path | None:
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

    def _git(
        self,
        *args: str,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        # ``timeout`` is opt-in (default ``None`` = unbounded, preserving
        # behaviour for every local plumbing call). Only the network
        # ``fetch`` in :meth:`_check_drift` sets it — see ``FETCH_TIMEOUT``
        # (HATS-711). A bounded ``fetch`` cannot wedge the per-branch
        # lifecycle lock and then mis-blame phantom concurrency on peers.
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.project_dir),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
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
            "-c",
            f"core.filesRefLockTimeout={REF_LOCK_TIMEOUT_MS}",
            "-c",
            f"core.packedRefsTimeout={REF_LOCK_TIMEOUT_MS}",
            *args,
            cwd=cwd,
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

    def _refuse_if_mid_merge(self) -> None:
        """Raise :class:`WorktreeMainRepoMidMergeError` if the main repo is
        mid-merge. **Call only while holding the base-branch lock.**

        HATS-587 / F4 + HATS-602. A concurrent peer ai-hats merge holds the
        base-branch lock for the duration of its ``git merge``; once we own
        that lock the peer's *transient* ``MERGE_HEAD`` is already gone, so
        only a genuinely-stuck FOREIGN ``MERGE_HEAD`` (an operator's
        half-finished IDE merge, an aborted run) trips the guard. The
        pre-HATS-602 placement checked this in :meth:`merge` OUTSIDE the
        lock, which false-positived on a peer's in-flight merge during two
        parallel merges into the same base (the HATS-602 flake). The raise
        still happens before any mutation (``git merge`` has not run yet),
        so the untouched-worktree contract holds.
        """
        if self._main_repo_mid_merge():
            raise WorktreeMainRepoMidMergeError(self.project_dir)

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
            self._git("fetch", "origin", self._original_branch, timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            # HATS-711: a hung fetch (dead VPN / DNS blackhole) must not wedge
            # merge() — bound it and fall through to the local-only check,
            # exactly like a fetch failure. Named explicitly so triage starts
            # at the network, not at a phantom concurrent peer.
            logger.warning(
                "Drift check: fetch origin %s timed out after %.0fs "
                "(slow/unreachable remote); proceeding with local-only check "
                "— remote-side drift will NOT be detected this run",
                self._original_branch,
                FETCH_TIMEOUT,
            )
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            stderr = (getattr(exc, "stderr", "") or "").strip()
            tail = stderr.splitlines()[-1] if stderr else "<no stderr>"
            logger.warning(
                "Drift check: fetch origin %s failed (%s); proceeding with "
                "local-only check — remote-side drift will NOT be detected "
                "this run",
                self._original_branch,
                tail,
            )

        try:
            current_local = self._git("rev-parse", self._original_branch).stdout.strip()
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

        lines = [f"Worktree base '{self._original_branch}' drifted since worktree was created."]
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
            # HATS-602: authoritative mid-merge guard, inside the base lock.
            self._refuse_if_mid_merge()
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "merge",
                "--squash",
                self.branch_name,
                project_dir=self.project_dir,  # HATS-486 stale-lock probe
            )
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "commit",
                "-m",
                f"feat(agent): {self.branch_name}",
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
            # HATS-602: authoritative mid-merge guard, inside the base lock.
            self._refuse_if_mid_merge()
            _retry_git_merge(
                self._git_with_ref_lock_wait,
                "merge",
                "--no-ff",
                self.branch_name,
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
                    "Worktree dir already absent (%s); git removal failed harmlessly",
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
                self.worktree_path,
                tail,
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

    def _classify_delete_branch_error(self, stderr: str) -> tuple[str, str] | None:
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
                    if (exc.stderr or "").strip()
                    else "<no stderr>",
                )
                return
            reason, tail = classified
            logger.warning(
                "Branch '%s' preserved (%s): %s",
                self.branch_name,
                reason,
                tail,
            )
            raise WorktreePartialCleanupError(
                self.branch_name,
                reason,
                tail,
            ) from exc
