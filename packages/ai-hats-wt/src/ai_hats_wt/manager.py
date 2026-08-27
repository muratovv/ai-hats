"""Git worktree isolation for sub-agent execution (HATS-004).

:class:`WorktreeManager` creates and manages linked git worktrees and their
create / merge / discard lifecycle. The lock & retry concurrency infrastructure
that serializes those operations — and the full lock-ordering model — lives in
:mod:`ai_hats_wt.locks` (extracted in HATS-715, moved to the standalone
``ai-hats-wt`` package in HATS-880).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, NoReturn, Protocol


from ai_hats_core import scrubbed_git_env
from ai_hats_core.deadline import Deadline

from .locks import (  # noqa: F401  -- re-export preserves the import surface (HATS-715)
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

#: Merge tombstones live under the state dir, in a SUBdirectory: `list_active`
#: globs `*.json` non-recursively, so this stays invisible to it (HATS-1664).
MERGED_SUBDIR = "merged"


class WorktreeDirtyError(Exception):
    """Raised when a destructive operation targets a worktree with uncommitted changes."""


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
    """Raised when the base holds commits the worktree branch never took in.

    HATS-457 / HYP-017: another agent's worktree merge — or an explicit
    ``git pull`` — landed commits on the base that this worktree's
    pre-merge verification never saw. HATS-1307: the test is *containment*
    (is the base an ancestor of the branch), so rebasing clears it; only a
    consciously accepted stale baseline still needs ``--accept-drift``.

    **Body contract (HATS-509)**: the exception message carries
    **facts only** — the drift summary built by ``_check_drift``
    (header, ``local:`` / ``remote:`` sections, ``affected paths:``
    listings). It MUST NOT include user-facing recipe text such as
    "re-run with ``--accept-drift``". The recipe is owned by CLI
    handlers (``cli/worktree.py wt_merge``, ``ai_hats/rack_cli_provider.py``)
    so each command surface can name its own flags — historically the
    literal trailer leaked into ``task transition done``, where the flag
    does NOT exist. The attributes below let those handlers name concrete
    refs without parsing the body.
    """

    def __init__(
        self,
        message: str,
        *,
        branch_name: str | None = None,
        base_branch: str | None = None,
        worktree_path: Path | None = None,
    ) -> None:
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.worktree_path = worktree_path
        super().__init__(message)


class WorktreeStaleRefError(Exception):
    """Raised when expected_tip SHA does not match live branch tip SHA at merge time.

    HATS-1346: validates that the task branch tip has not moved since expected/prepared ref.
    """

    def __init__(self, branch_name: str, expected_tip: str, current_tip: str) -> None:
        self.branch_name = branch_name
        self.expected_tip = expected_tip
        self.current_tip = current_tip
        super().__init__(
            f"Stale ref refusal for '{branch_name}': expected tip {expected_tip}, "
            f"but current branch tip is {current_tip}."
        )


class WorktreeMergeIncompleteError(Exception):
    """Raised when post-merge verification shows target base branch does not contain branch tip.

    HATS-1346: prevents deleting worktree dir and branch if branch tip was not fully integrated.
    """

    def __init__(self, branch_name: str, tip_sha: str, base_branch: str) -> None:
        self.branch_name = branch_name
        self.tip_sha = tip_sha
        self.base_branch = base_branch
        super().__init__(
            f"Merge incomplete for '{branch_name}': base branch '{base_branch}' does not contain "
            f"branch tip {tip_sha}. Worktree and branch preserved."
        )


class WorktreeRebasedBranchError(Exception):
    """Raised when a branch's commits are already patch-integrated into base under different SHAs.

    HATS-1370: prevents automatic re-merging of rebased commits (which would create duplicate
    commit history on base). Refuses by default unless accept_drift=True or force=True is passed.
    Body contract (HATS-509): facts only in message.
    """

    def __init__(
        self,
        branch_name: str,
        base_branch: str,
        *,
        worktree_path: Path | None = None,
    ) -> None:
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.worktree_path = worktree_path
        super().__init__(
            f"Branch '{branch_name}' commits are already integrated into base '{base_branch}' "
            f"under different SHAs (rebased). Automatic merge refused to prevent duplicate history."
        )


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
            f"Refused: main repo HEAD is '{current}', not the worktree "
            f"merge target ({', '.join(canonical)}). Worktrees inherit their "
            f"merge target from the current branch — creating one from another "
            f"branch leads to merges landing on the wrong branch (HATS-518). "
            f"Run `git checkout {canonical[0]}` in the main repo first, then retry."
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


class WorktreeMergeConflictError(Exception):
    """``git merge`` stopped on conflicting content, and the main checkout was
    put back where it was (HATS-1651).

    A conflict is the one merge failure git reports by *leaving work behind*:
    ``MERGE_HEAD`` written, markers in the tree, the index half-resolved. Before
    this class the resulting :class:`subprocess.CalledProcessError` fell into the
    generic handler in :meth:`WorktreeManager.merge`, which logged "worktree and
    branch left intact for retry" — true of the worktree, silent about the main
    checkout it had just left mid-merge. The next invocation then refused with
    :class:`WorktreeMainRepoMidMergeError`, telling the operator to clean up after
    an operation the tool said it never performed.

    Raised only once the rollback is VERIFIED. The unverified case is
    :class:`WorktreeMergeLeftoverError`, and the two must never be collapsed —
    the whole point is that a cleanup is reported because it was observed.
    Facts only, no recipe (HATS-509 contract): the CLI owns what to do next.
    """  # comment-length: allow — which of the two classes applies IS the contract

    def __init__(self, branch_name: str, base_branch: str, paths: tuple[str, ...]) -> None:
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.paths = paths
        listed = "\n".join(f"    {path}" for path in paths) or "    (git named no path)"
        super().__init__(
            f"Merging '{branch_name}' into '{base_branch}' conflicts on:\n{listed}\n"
            f"The merge was rolled back: '{base_branch}' and the main checkout are as "
            f"they were before it started, and the worktree and branch are untouched."
        )


class WorktreeMergeLeftoverError(Exception):
    """The merge failed AND the main checkout could not be put back (HATS-1651).

    ``git merge --abort`` is documented as unable to reconstruct pre-merge
    uncommitted changes in some cases, and ``--squash`` writes no ``MERGE_HEAD``
    for it to work from at all. So the rollback is attempted, then *verified*, and
    when the verification fails this names what survived. Claiming a cleanup that
    did not happen is the defect this card exists to remove, so the honest report
    of a failed rollback is a louder refusal, never a quieter one.
    """

    def __init__(
        self,
        branch_name: str,
        base_branch: str,
        leftover: tuple[str, ...],
        paths: tuple[str, ...] = (),
    ) -> None:
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.leftover = leftover
        self.paths = paths
        conflicted = (
            "\nIt conflicted on:\n" + "\n".join(f"    {path}" for path in paths) if paths else ""
        )
        listed = "\n".join(f"    {item}" for item in leftover)
        super().__init__(
            f"Merging '{branch_name}' into '{base_branch}' failed, and the main checkout "
            f"could NOT be returned to its pre-merge state.{conflicted}\n"
            f"What is left behind:\n{listed}"
        )


@dataclass(frozen=True)
class _MainState:
    """The main checkout in the only terms a merge rollback has to restore
    (HATS-1651): where the branch points, what is staged, what is unresolved."""

    head: str
    staged: dict[str, str]
    unmerged: tuple[str, ...]


#: Branch names considered "canonical bases" for worktree creation. The
#: first one that actually exists in the repo is the comparison target.
#: Hardcoded by design (HATS-518): 99% repo coverage, no new config
#: primitive until a second use case appears (YAGNI / design-minimalism).
CANONICAL_BASE_BRANCHES: tuple[str, ...] = ("master", "main")


def assert_head_is_canonical_base(project_dir: Path, merge_target: str | None = None) -> None:
    """Refuse if main-repo HEAD is not on the worktree merge target.

    ``merge_target`` (HATS-942): ``None`` => today's set-membership against
    :data:`CANONICAL_BASE_BRANCHES` (byte-identical); a configured value
    (already validated to exist by the resolver) => HEAD must equal it exactly.
    No-op on: non-git dir; detached HEAD; and — default only — a repo with no
    canonical branch (exotic naming, pass through rather than block).

    :raises WorktreeBaseBranchError: HEAD is on a named branch and does not
        match the resolved target (configured branch, or any existing canonical).
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
            env=scrubbed_git_env(),
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return  # Can't introspect — fall through to existing behavior.

    # Detached HEAD: `rev-parse --abbrev-ref HEAD` returns the literal
    # string "HEAD". No branch name to compare; skip the guard rather
    # than block (operator on a SHA knows what they're doing).
    if head == "HEAD":
        return

    # HATS-942: a configured merge_target narrows the guard to that single
    # branch (resolver already validated it exists).
    if merge_target is not None:
        if head == merge_target:
            return
        raise WorktreeBaseBranchError(current=head, canonical=[merge_target])

    existing_canonical: list[str] = []
    for name in CANONICAL_BASE_BRANCHES:
        try:
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", name],
                cwd=str(project_dir),
                capture_output=True,
                check=True,
                env=scrubbed_git_env(),
            )
            existing_canonical.append(name)
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue

    if not existing_canonical:
        return  # No canon in this repo — nothing to compare against.

    if head in existing_canonical:
        return

    raise WorktreeBaseBranchError(current=head, canonical=existing_canonical)


def _current_head_branch(project_dir: Path) -> str:
    """``git rev-parse --abbrev-ref HEAD`` — the branch name, or ``"HEAD"`` when
    detached. Raises on a non-git dir (callers gate on ``_check_is_git`` first)."""
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        check=True,
        env=scrubbed_git_env(),
    ).stdout.strip()


def get_default_base_branch(project_dir: Path) -> str:
    """HATS-942: the branch a worktree is cut FROM when `worktree.base_branch` is
    unset — the current HEAD branch (git's implicit start-point today)."""
    return _current_head_branch(project_dir)


def get_default_merge_branch(project_dir: Path) -> str:
    """HATS-942: the branch `wt merge` lands INTO when `worktree.merge_target` is
    unset — the current HEAD branch (today's `_original_branch`). Coincides with
    :func:`get_default_base_branch` at HEAD; named apart as they are distinct knobs."""
    return _current_head_branch(project_dir)


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


# ------------------------------------------------------------------
# Lifecycle extension-point contract (ADR-0013 D2/D3/D8)
#
# The hook-agnostic core fires these extension-points at each lifecycle
# site; ai-hats injects a bundle that decides WHAT runs (worktree hooks),
# the fail-vs-warn policy, and the skip/legacy escapes. A bare core uses
# the no-op default and runs no hooks.
# ------------------------------------------------------------------


class WorktreeTeardownAborted(Exception):
    """A ``before_teardown`` extension-point vetoed teardown (ADR-0013 D8).

    Hook-agnostic: the core knows only that a teardown route was aborted —
    never why. ai-hats raises this from its hook-running bundle on a
    fail-closed ``wt_out`` failure, with the hook error riding as ``__cause__``.
    Per-route control-flow is core-owned: ``merge`` / ``discard`` propagate it
    (FSM/CLI surface it, HATS-481); ``cleanup`` suppresses it (warn + preserve
    + return, so a sub-agent's original error is not masked).
    """


class WorktreeMergeAborted(Exception):
    """A ``before_merge`` extension-point vetoed the merge (HATS-1540 / ADR-0019).

    The sibling of :class:`WorktreeTeardownAborted`, and hook-agnostic the same
    way: the core knows a merge was refused, never by what. Deliberately NOT the
    teardown veto reused — that one fires after the merge commit exists and can
    only strand a worktree (ADR-0012 / HATS-775 rejected it as a gate), while
    this one fires before any mutation and leaves the tree exactly as it was.
    """


@dataclass(frozen=True)
class Blocker:
    """One reason :meth:`WorktreeManager.merge` would refuse right now (HATS-1654).

    ``kind`` names the guard (``drift``, ``dirty``, …) so a caller can drop the
    one that already raised; ``message`` is the refusal's own words.
    """

    kind: str
    message: str


@dataclass(frozen=True)
class LifecycleContext:
    """What a lifecycle extension-point needs — and nothing hook-policy (D2).

    ``carry`` is the opaque persisted hook record (the core stores/replays it
    verbatim, D5); ``legacy`` distinguishes an absent carry (state predates
    wt-hooks) from an empty ``{}`` so the bundle can warn-not-drop. ``state_dir``
    is the manager's injected state/lock path-base (ADR-0013 D4); the bundle
    resolves hook-log paths off it so state + hook-logs share one base even when a
    driver injects a custom base (HATS-851).
    """

    worktree_path: Path | None
    project_dir: Path
    state_dir: Path
    branch_name: str
    carry: dict[str, list[dict[str, Any]]]
    skip_hooks: bool
    legacy: bool
    #: Budget of the lock this site holds (HATS-1593). The bundle draws every
    #: hook timeout through it, so N hooks share one ceiling.
    deadline: Deadline


class WorktreeLifecycle(Protocol):
    """Core lifecycle extension-points (ADR-0013 D2). Default impl is no-op.

    ``on_created`` fires once after ``git worktree add`` (warn-continue — it
    must never raise). ``before_merge`` fires in ``merge()`` after the cheap
    local guards and before any mutation; raising
    :class:`WorktreeMergeAborted` refuses the merge with the tree untouched.
    ``before_teardown`` fires at every teardown route just before
    ``_remove_worktree``; raising :class:`WorktreeTeardownAborted` aborts the
    route fail-closed.
    """

    def on_created(self, ctx: LifecycleContext) -> None: ...

    def before_merge(self, ctx: LifecycleContext) -> None: ...

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None: ...


class _NoopLifecycle:
    """Hook-agnostic default: a bare core runs no hooks (ADR-0013 D2)."""

    def on_created(self, ctx: LifecycleContext) -> None:
        return None

    def before_merge(self, ctx: LifecycleContext) -> None:
        return None

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        return None


#: The default bundle injected when no ai-hats hook-runner is supplied.
NOOP_LIFECYCLE: WorktreeLifecycle = _NoopLifecycle()


#: Bare-core fallback for the state/lock path-base — a project-local dir, so the
#: core needs no ``ai_hats.paths`` import (ADR-0013 D4, lets the D6 import-lint
#: forbid it). This RUNTIME dir is unrelated to the ``ai_hats_wt`` code
#: PACKAGE (HATS-880).
_DEFAULT_STATE_DIRNAME = ".wt"


def _resolve_state_dir(
    project_dir: Path,
    state_dir: Path | None,
    lifecycle: WorktreeLifecycle,
) -> Path:
    """Resolve the worktree state/lock path-base (ADR-0013 D4).

    ai-hats injects its ``worktrees_dir(project_dir)`` convention; a bare core
    omits it and falls back project-local. The fallback fails *silent* (a
    wrong-but-valid path the import-lint can't catch — it is a runtime arg), so
    an ai-hats driver (any non-no-op ``lifecycle``) that omits the base is a bug
    that would de-serialize the cross-process locks (ADR-0006). The
    ``__debug__`` assert (D4 "Variant A") catches that omission loud rather than
    silently mis-locating state.
    """
    assert lifecycle is NOOP_LIFECYCLE or state_dir is not None, (
        "ADR-0013 D4: a non-no-op WorktreeLifecycle requires an explicit "
        "state_dir base (omitting it de-serializes the cross-process locks)"
    )
    return state_dir if state_dir is not None else project_dir / _DEFAULT_STATE_DIRNAME


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
        base_branch: str | None = None,
        merge_target: str | None = None,
        lifecycle: WorktreeLifecycle = NOOP_LIFECYCLE,
        state_dir: Path | None = None,
        worktree_checkouts_dir: Path | None = None,
        git_timeout: float | None = None,
    ) -> None:
        self.project_dir = project_dir
        self.role_name = role_name
        self.session_id = session_id
        self.isolation_mode = isolation_mode
        # HATS-1015 liveness budget (opt-in, None = unbounded — unchanged
        # default): the per-git wall-clock ceiling every ``_git`` call falls
        # back to, so a hung local plumbing op cannot hold a caller's lock.
        self._git_timeout = git_timeout
        # HATS-942: base_branch = start-point (None => HEAD); merge_target =
        # where `wt merge` lands (None => HEAD-following canonical). Trusted to be
        # existing branches — the caller validates via resolve_worktree_branches;
        # garbage still fails loud at `git worktree add` (no silent wrong-branch).
        self._base_branch = base_branch
        self._merge_target = merge_target
        self._resolved_base_branch: str | None = None  # concrete start-point, set in create()
        self.worktree_path: Path | None = None
        # HATS-827: backstop — empty role yields the git-invalid branch
        # agent//<sid>; fail at construction, not deep in create().
        if not branch_name and not role_name:
            raise ValueError("cannot build worktree branch: empty role segment — pass a role")
        self.branch_name = branch_name or f"agent/{role_name}/{session_id}"
        self._is_git = False
        self._original_branch: str | None = None
        self._base_sha_at_create: str | None = None  # HATS-457
        # HATS-823: create-time carry {wt_in/wt_out: [{skill, script, on}]},
        # persisted to state and replayed at teardown (never recomposed).
        self._wt_hooks: dict[str, list[dict[str, Any]]] = {}
        # HATS-823: state predates wt-hooks (key absent, not empty {}) → WARN.
        self._wt_hooks_legacy = False
        # ADR-0013 D3: the lifecycle extension-point bundle. ai-hats injects its
        # hook-running bundle; a bare core keeps the no-op default.
        self._lifecycle = lifecycle
        # ADR-0013 D4: the state/lock path-base. ai-hats passes worktrees_dir;
        # a bare core falls back project-local (no ai_hats.paths import).
        self._state_dir = _resolve_state_dir(project_dir, state_dir, lifecycle)
        # HATS-1632: where create() mints the tree. Same D4 shape as state_dir —
        # ai-hats passes worktree_checkouts_dir; None keeps the mkdtemp fallback.
        self._worktree_checkouts_dir = worktree_checkouts_dir

    def create(
        self,
        *,
        wt_hooks: dict[str, list[dict[str, Any]]] | None = None,
        outer_deadline: Deadline | None = None,
    ) -> Path:
        """Create an isolated worktree. Returns project_dir if not a git repo
        or if isolation_mode is NONE (no worktree, runs in project_dir).

        ``outer_deadline`` is the enclosing caller's ceiling, as in :meth:`merge`
        — the ``-> execute`` edge reaches here in-lock, so the ``wt_in`` hooks
        are bounded by the rack task lock too (HATS-1603).

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
        # HATS-942: derive the concrete base (start-point) + merge target at
        # create-start. Configured value wins; else the default = current HEAD.
        self._resolved_base_branch = self._base_branch or get_default_base_branch(self.project_dir)
        self._original_branch = self._merge_target or get_default_merge_branch(self.project_dir)
        # HATS-457: snapshot the merge target — the "did it move since I last
        # verified" half of the drift check. HATS-1307: still load-bearing in
        # the fork shape (base != merge_target), where the target is never an
        # ancestor of the branch and containment alone cannot date the change.
        try:
            self._base_sha_at_create = self._git("rev-parse", self._original_branch).stdout.strip()
        except subprocess.CalledProcessError:
            self._base_sha_at_create = None

        # HATS-479 — L1 + L2 + L4. See module docstring "Create-time concurrency".
        with _acquire_create_lock(self._state_dir):
            # L2: re-check under the lock. Closes the TOCTOU window between a
            # caller's optional pre-check and our work.
            existing = WorktreeManager.load_for_branch(
                self.project_dir, self.branch_name, state_dir=self._state_dir
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
            # symptom "git worktree add -b fails with 'already exists'":
            #   Case C — branch is a LINKED worktree but state JSON is gone
            #            (manual delete / restore): adopt the path, persist
            #            fresh state.
            #   Case B — branch checked out in the MAIN worktree: refuse —
            #            adopting project_dir silently disables auto-merge in
            #            _teardown_worktree (FSM contract divergence).
            #   Case A — branch exists but unowned: attach a new linked
            #            worktree (positional `git worktree add`, no -b).
            # C/B are detected via `git worktree list`, A is the residual;
            # classifier sits inside L1 so HATS-479 mutex invariants hold.
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
            base = self._worktree_checkouts_dir
            if base is not None:
                base.mkdir(parents=True, exist_ok=True)
            # dir=None keeps the historical temp root for a bare core (D9).
            tmpdir = tempfile.mkdtemp(prefix=f"ai-hats-wt-{prefix}-", dir=base)
            self.worktree_path = Path(tmpdir)

            try:
                _retry_worktree_add(
                    self._git,
                    self.branch_name,
                    self.worktree_path,
                    create_branch=not attach_existing_branch,
                    start_point=self._resolved_base_branch,  # HATS-942: cut from base
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
        # HATS-1593: and outside the repo-wide create lock, whose budget is a
        # quarter of the hook's. Acquired after that lock is released, so no
        # L3+L1 co-hold arises (ADR-0006).
        state_path = self._state_dir / f"{_state_key(self.branch_name)}.json"
        with _acquire_lifecycle_lock(state_path, outer=outer_deadline) as deadline:
            self._fire_on_created(deadline)
        logger.info(
            "Created worktree %s on branch %s",
            self.worktree_path,
            self.branch_name,
        )
        return self.worktree_path

    def probe_blockers(self, *, force: bool = False, accept_drift: bool = False) -> list[Blocker]:
        """Every refusal :meth:`merge` would raise right now, in its own order.

        HATS-1654: the guards fire one per run, so a merge can cost three runs to
        learn three facts. This asks all of them at once — read-only, no lock, no
        mutation; the one cost is the drift check's bounded ``git fetch``. The
        ``wt:pre-merge`` point is NOT probed: a check is an arbitrary command with
        no "would you refuse?" mode (that predicate is HATS-1615's).
        """
        if not self._is_git or self.worktree_path is None or not self.worktree_path.exists():
            return []
        base = self._original_branch
        if base is None:
            return [Blocker("state", str(WorktreeStateIncompleteError(self.branch_name)))]

        try:
            tip = self._git("rev-parse", self.branch_name).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return [Blocker("branch", f"could not read the tip of '{self.branch_name}': {exc}")]

        base_exists = self._branch_exists(base)
        if base_exists and self._is_ancestor(tip, base):
            return []  # already merged — merge() tears down, it does not refuse

        def rebased() -> str | None:
            if not self._is_patch_integrated(self.branch_name, base):
                return None
            return str(WorktreeRebasedBranchError(self.branch_name, base))

        def base_mismatch() -> str | None:
            head = self._get_current_branch()
            if head == base:
                return None
            return str(WorktreeBaseBranchMismatchError(current=head, expected=base))

        def dirty() -> str | None:
            try:
                self._check_clean()
            except WorktreeDirtyError as exc:
                return str(exc)
            return None

        def drift() -> str | None:
            try:
                self._check_drift()
            except WorktreeDriftError as exc:
                return str(exc)
            return None

        # One row per guard, in `merge()`'s order; the flag column is the same
        # bypass the guard itself honours, so a bypassed guard is never probed.
        probes: tuple[tuple[str, bool, Callable[[], str | None]], ...] = (
            ("rebased", accept_drift or force, rebased),
            ("base-mismatch", not base_exists, base_mismatch),
            ("dirty", force, dirty),
            ("drift", accept_drift, drift),
        )

        blockers: list[Blocker] = []
        for kind, skipped, probe in probes:
            if skipped:
                continue
            try:
                message = probe()
            except Exception as exc:  # noqa: BLE001 — one broken probe must not blind the rest
                blockers.append(Blocker(kind, f"could not check: {exc!r}"))
                continue
            if message:
                blockers.append(Blocker(kind, message))
        return blockers

    def merge(
        self,
        *,
        squash: bool = False,
        force: bool = False,
        accept_drift: bool = False,
        skip_hooks: bool = False,
        expected_tip: str | None = None,
        outer_deadline: Deadline | None = None,
    ) -> None:
        """Merge worktree changes back into the original branch and clean up.

        HATS-1603: ``outer_deadline`` is the enclosing caller's ceiling (the rack
        task lock, when the FSM automerges) — every budget drawn inside is
        clamped to it. ``None`` is the direct ``ai-hats wt merge`` road: no
        enclosing lock, so the lifecycle lock's own deadline stands.

        Raises WorktreeDirtyError if the worktree has uncommitted changes
        unless force=True (HATS-062).

        Raises WorktreeDriftError if the original branch moved between
        worktree create and merge (locally or on the remote) unless
        accept_drift=True (HATS-457 / HYP-017). ``force`` deliberately
        does not bypass drift — the two checks address different risks
        (uncommitted changes vs stale baseline).

        Raises WorktreeStaleRefError if expected_tip is provided and does not
        match the live task branch tip SHA (HATS-1346).

        Raises WorktreeMergeIncompleteError if post-merge verification shows
        the target base branch does not contain the task branch tip SHA (HATS-1346).

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

        state_path = self._state_dir / f"{_state_key(self.branch_name)}.json"
        with _acquire_lifecycle_lock(state_path, outer=outer_deadline) as deadline:
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

            # HATS-1346: resolve current task branch tip at merge time.
            current_tip_sha = self._git("rev-parse", self.branch_name).stdout.strip()
            if expected_tip is not None:
                expected_sha = (
                    self._git("rev-parse", expected_tip).stdout.strip()
                    if self._branch_exists(expected_tip)
                    else expected_tip.strip()
                )
                if current_tip_sha != expected_sha:
                    raise WorktreeStaleRefError(
                        branch_name=self.branch_name,
                        expected_tip=expected_sha,
                        current_tip=current_tip_sha,
                    )

            # HATS-596: checkout-independent already-merged short-circuit.
            # The task lives on its own branch; the main checkout may be on
            # ANY branch. If the task-branch tip is already an ancestor of the
            # recorded base, the work is integrated — `git merge` is a no-op.
            # Because no merge runs, the main-repo HEAD is irrelevant, so this
            # MUST precede the wandered-HEAD (HATS-533) and foreign-MERGE_HEAD
            # (HATS-587/F4) guards — both would FALSE-refuse already-merged
            # work (merged+pushed while main sat on a feature branch).
            # `_check_clean` is still honored (force-bypassable) so uncommitted
            # edits aren't dropped; drift is skipped (work already integrated).
            # Recorded local base only — origin/<base> out of scope.
            if self._original_branch is not None and self._branch_exists(self._original_branch):
                if self._is_ancestor(current_tip_sha, self._original_branch):
                    if not force:
                        self._check_clean()
                    # HATS-823: short-circuit still destroys the dir → harvest first.
                    # No `pre-merge`: nothing is published, so the precondition has
                    # nothing to hold (ADR-0019 D3; test_wt_pre_merge_point.py, HATS-1595).
                    self._fire_before_teardown("merge", deadline, skip_hooks=skip_hooks)
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

                # HATS-1370: rebased/patch-integrated branch check.
                # If all commits in branch are already patch-equivalent on base (git cherry),
                # running `git merge` would pull duplicate pre-rebase commits into base.
                # Refuse by default (WorktreeRebasedBranchError) unless accept_drift=True or force=True.
                if self._is_patch_integrated(self.branch_name, self._original_branch):
                    if not accept_drift and not force:
                        raise WorktreeRebasedBranchError(
                            self.branch_name,
                            self._original_branch,
                            worktree_path=self.worktree_path,
                        )
                    if not force:
                        self._check_clean()
                    # No `pre-merge` here either, same recorded decision and same
                    # reason: the merge is skipped, so nothing is published
                    # (ADR-0019 D3; test_wt_pre_merge_point.py, HATS-1595).
                    self._fire_before_teardown("merge", deadline, skip_hooks=skip_hooks)
                    self._remove_worktree()
                    self._delete_branch()
                    self._clear_state()
                    self.worktree_path = None
                    logger.info(
                        "Worktree '%s' already integrated into '%s' via rebase/patch-equivalence — "
                        "torn down without re-merge (HATS-1370)",
                        self.branch_name,
                        self._original_branch,
                    )
                    return

            # HATS-533: refuse if main-repo HEAD has wandered off the merge
            # target captured at create time (manual checkout, peer agent in
            # main repo, IDE switch) — `git merge` runs in main-repo cwd, so a
            # wrong HEAD silently merges into the wrong branch (HATS-486 class).
            # Ordering (do not flip): AFTER the lifecycle lock + worktree-exists
            # peer no-op (a completed peer teardown must no-op regardless of
            # HEAD) and AFTER the HATS-596 short-circuit (no merge → no risk);
            # BEFORE _check_clean/_check_drift/merge (all answer the wrong
            # question with HEAD wrong). Skipped when _original_branch is None
            # or the recorded base no longer exists — both owned by the
            # OriginalBranchMissing guard below (keeps
            # test_merge_raises_when_original_branch_deleted green).
            if self._original_branch is not None and self._branch_exists(self._original_branch):
                head = self._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
                if head != self._original_branch:
                    raise WorktreeBaseBranchMismatchError(
                        current=head, expected=self._original_branch
                    )

            # HATS-587/F4 mid-merge guard lives INSIDE the base lock now
            # (HATS-602: _refuse_if_mid_merge in _fast_forward_merge /
            # _squash_merge), not here — checking outside false-positived on a
            # peer merge's transient MERGE_HEAD (the HATS-602 flake). The
            # HEAD-mismatch guard above stays here: a peer merge never moves
            # the main-repo branch pointer, so it has no concurrency FP.

            if not force:
                self._check_clean()
            if not accept_drift:
                self._check_drift()

            # HATS-1540 / ADR-0019: the `wt:pre-merge` extension-point. AFTER the
            # cheap local guards so a broken check cannot mask a dirty tree or a
            # drifted base, and BEFORE every mutation below — a refusal leaves
            # the worktree, the branch and the base exactly as they were. Fires
            # regardless of the caller: suppressing it for the FSM automerge
            # would be caller-aware coupling, and the FSM path is one of the two
            # roads into master this point exists to hold.
            self._fire_before_merge(deadline, skip_hooks=skip_hooks)

            if self._original_branch and not self._branch_exists(self._original_branch):
                self._fire_before_teardown("merge", deadline, skip_hooks=skip_hooks)
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
            except (
                WorktreeMainRepoMidMergeError,
                WorktreeStaleRefError,
                WorktreeMergeIncompleteError,
                WorktreeMergeConflictError,
                WorktreeMergeLeftoverError,
            ):
                # HATS-602 / HATS-1346: precondition & containment refusals — no git merge retry,
                # propagate cleanly so the caller surfaces the actionable hint.
                # HATS-1651: the two conflict refusals join them because each has
                # already established the main checkout's state and said so — the
                # generic log below would append a second, vaguer account of it.
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

            # HATS-1346: post-merge containment verification before teardown.
            if self._original_branch and self._branch_exists(self._original_branch):
                is_integrated = (
                    self._is_ancestor(current_tip_sha, self._original_branch)
                    if not squash
                    else (
                        self._git("diff", current_tip_sha, self._original_branch).stdout.strip()
                        == ""
                    )
                )
                if not is_integrated:
                    raise WorktreeMergeIncompleteError(
                        branch_name=self.branch_name,
                        tip_sha=current_tip_sha,
                        base_branch=self._original_branch,
                    )

            # HATS-823: harvest before teardown. On failure the branch survives,
            # so a retry hits the HATS-596 short-circuit and re-runs the hook.
            self._fire_before_teardown("merge", deadline, skip_hooks=skip_hooks)
            self._remove_worktree()
            self._delete_branch()
            # HATS-1664: read it before _clear_state drops the only record.
            merged_sha = self._merge_commit_sha()
            self._clear_state()
            self._record_merged(merged_sha)
            # Match discard() / cleanup() teardown contract: a successful
            # merge invalidates self for any further lifecycle ops.
            self.worktree_path = None

    def discard(
        self,
        *,
        force: bool = False,
        force_remove: bool = False,
        skip_hooks: bool = False,
        outer_deadline: Deadline | None = None,
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
        :param outer_deadline: the enclosing caller's ceiling, as in
            :meth:`merge` — the failed/cancelled edges reach here from
            inside the rack task lock (HATS-1603).
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = self._state_dir / f"{_state_key(self.branch_name)}.json"
        with _acquire_lifecycle_lock(state_path, outer=outer_deadline) as deadline:
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
            self._fire_before_teardown("discard", deadline, skip_hooks=skip_hooks)
            self._remove_worktree(force_rmtree=force_remove)
            self._delete_branch()
            self.worktree_path = None
            self._clear_state()

    def reclaim_if_clean(self, *, has_extra_hold: Callable[[Path], bool] | None = None) -> bool:
        """Discard this worktree IFF it carries no unmerged work (HATS-979).

        An epicified task's execute-time worktree is dead weight ("epics never
        get a worktree"). Reclaim it only when safe — KEPT (logged) on: a dirty
        tree, own commits not yet in the canonical base, or a caller-supplied
        ``has_extra_hold`` predicate — the injected seam for gitignored state the
        git checks can't see (e.g. pending hunk review; the standalone engine
        stays unaware of it). True when discarded, else False.
        """
        if not self._is_git or self.worktree_path is None:
            return False
        if self._has_uncommitted_changes():
            logger.info("Worktree '%s' kept — uncommitted changes (HATS-979)", self.branch_name)
            return False
        if has_extra_hold is not None and has_extra_hold(self.worktree_path):
            logger.info("Worktree '%s' kept — caller-flagged hold (HATS-979)", self.branch_name)
            return False
        if self.branch_merged_into_canonical_base(self.project_dir, self.branch_name) is None:
            logger.info("Worktree '%s' kept — unmerged commits (HATS-979)", self.branch_name)
            return False
        self.discard(force=False)
        return True

    def _has_uncommitted_changes(self) -> bool:
        """Non-raising twin of ``_check_clean``: True on any uncommitted change.

        On a probe failure returns True (conservative — keep, never risk
        discarding work that could not be inspected).
        """
        if self.worktree_path is None:
            return False
        try:
            result = self._git("status", "--porcelain", cwd=self.worktree_path)
        except subprocess.CalledProcessError:
            return True
        return bool(result.stdout.strip())

    def cleanup(self, *, force_discard: bool = False, skip_hooks: bool = False) -> None:
        """Clean up worktree. Merges changes based on isolation_mode.

        HATS-480: holds the per-wt-branch lifecycle lock through the
        entire body. A concurrent direct ``wt discard``/``wt merge`` on
        the same branch (issued by another agent / CLI while the
        context-manager is winding down) serializes against this call.
        """
        if not self._is_git or self.worktree_path is None:
            return

        state_path = self._state_dir / f"{_state_key(self.branch_name)}.json"
        with _acquire_lifecycle_lock(state_path) as deadline:
            # HATS-480 idempotency re-check — see merge() / discard().
            if not self.worktree_path.exists():
                logger.info(
                    "Worktree '%s' already torn down by a peer — no-op",
                    self.branch_name,
                )
                return

            mode = IsolationMode.DISCARD if force_discard else self.isolation_mode

            # HATS-1540, recorded decision (supervisor ruling 2026-08-09): this
            # squash publishes to the base branch and DOES NOT fire
            # `wt:pre-merge`. ADR-0019 asks every path reaching a point to carry
            # either a test that the check fires or a recorded decision that it
            # must not; this is the latter, pinned by
            # `test_the_squash_cleanup_path_does_not_fire_the_point`.
            # Firing here would be worse than not firing: `cleanup` SUPPRESSES a
            # lifecycle veto by design (ADR-0013 D8 — a sub-agent's own error
            # must not be masked), so a refusal would be swallowed and the gate
            # would look armed while passing everything. Making it
            # non-suppressible is a change to D8's contract, not to this call
            # site. Revisit together: behaviour and ADR in one change.
            try:
                if mode == IsolationMode.SQUASH:
                    self._squash_merge()
            except Exception:
                logger.warning("Merge failed, falling back to branch mode", exc_info=True)
                mode = IsolationMode.BRANCH

            # ADR-0013 D8: auto path — a teardown abort must NOT raise over the
            # agent's original error; suppress (warn + preserve + return). The
            # core stays hook-agnostic: it logs the abort message verbatim (the
            # ai-hats bundle authored the wt_out-specific recovery recipe into it).
            try:
                self._fire_before_teardown("cleanup", deadline, skip_hooks=skip_hooks)
            except WorktreeTeardownAborted as exc:
                logger.warning(
                    "Teardown aborted at cleanup of '%s' — worktree preserved: %s",
                    self.branch_name,
                    exc,
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
    # Lifecycle extension-point firing (ADR-0013 D2/D3; hooks run ai-hats-side)
    # ------------------------------------------------------------------

    def _lifecycle_ctx(self, deadline: Deadline, *, skip_hooks: bool = False) -> LifecycleContext:
        """Snapshot the hook-agnostic context the extension-point bundle reads.

        ``carry`` is the opaque persisted hook record (D5); ``legacy`` lets the
        bundle warn-not-drop on a pre-wt-hooks state.
        """
        return LifecycleContext(
            worktree_path=self.worktree_path,
            project_dir=self.project_dir,
            state_dir=self._state_dir,
            branch_name=self.branch_name,
            carry=self._wt_hooks,
            skip_hooks=skip_hooks,
            legacy=self._wt_hooks_legacy,
            deadline=deadline,
        )

    def _fire_on_created(self, deadline: Deadline) -> None:
        """Fire the create extension-point (ADR-0013 D2/D3).

        Warn-continue: the bundle never raises here, so a create-time hook
        failure is friction, not an aborted create. A bare core (no-op bundle)
        runs nothing.
        """
        self._lifecycle.on_created(self._lifecycle_ctx(deadline))

    def _fire_before_merge(self, deadline: Deadline, *, skip_hooks: bool = False) -> None:
        """Fire the pre-merge extension-point (HATS-1540).

        A raised :class:`WorktreeMergeAborted` propagates: nothing below it has
        run, so the caller's refusal is total. Only ``merge()`` fires it —
        ``discard`` publishes nothing to a base branch and deliberately has no
        pre-operation point of its own.
        """
        self._lifecycle.before_merge(self._lifecycle_ctx(deadline, skip_hooks=skip_hooks))

    def _fire_before_teardown(
        self, event: str, deadline: Deadline, *, skip_hooks: bool = False
    ) -> None:
        """Fire a teardown extension-point before ``_remove_worktree`` (D3).

        A raised :class:`WorktreeTeardownAborted` aborts the route fail-closed;
        per-route handling (merge/discard propagate, ``cleanup`` suppresses) is
        owned by the calling teardown method, not by the bundle.
        """
        self._lifecycle.before_teardown(event, self._lifecycle_ctx(deadline, skip_hooks=skip_hooks))

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def save_state(self, *, key: str | None = None) -> Path:
        """Persist worktree state to ``<state_dir>/<key>.json`` (locked, atomic).

        ``state_dir`` is the injected path-base (ADR-0013 D4): ai-hats passes its
        ``<ai_hats_dir>/sessions/worktrees/`` convention; a bare core uses the
        project-local fallback.
        """
        k = key or _state_key(self.branch_name)
        state_dir = self._state_dir
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
        state_path = self._state_dir / f"{k}.json"
        with _acquire(state_path):
            try:
                state_path.unlink()  # safe-delete: ok worktree-state (git-managed)
            except FileNotFoundError:
                pass

    def _merge_commit_sha(self) -> str | None:
        """The commit the merge just landed on the base branch."""
        if not self._original_branch:
            return None
        try:
            return self._git("rev-parse", "--verify", self._original_branch).stdout.strip() or None
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
            # Not fatal: the merge itself succeeded. A missing tombstone costs the
            # ->done gate a run it could have skipped, never a wave-through.
            logger.warning("HATS-1664: merge sha unresolved for %s: %s", self.branch_name, exc)
            return None

    def _record_merged(self, merge_sha: str | None, *, key: str | None = None) -> None:
        """Leave the merge commit where :meth:`peek_merged_sha` can find it."""
        if not merge_sha:
            return
        k = key or _state_key(self.branch_name)
        tomb_dir = self._state_dir / MERGED_SUBDIR
        tomb_dir.mkdir(parents=True, exist_ok=True)
        tomb = tomb_dir / f"{k}.json"
        with _acquire(tomb):
            _atomic_write_json(tomb, {"branch": self.branch_name, "merge_sha": merge_sha})

    @classmethod
    def load_for_task(
        cls,
        project_dir: Path,
        task_id: str,
        *,
        lifecycle: WorktreeLifecycle = NOOP_LIFECYCLE,
        state_dir: Path | None = None,
        git_timeout: float | None = None,
    ) -> WorktreeManager | None:
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
        return cls._load_by_key(
            project_dir, key, lifecycle=lifecycle, state_dir=state_dir, git_timeout=git_timeout
        )

    @classmethod
    def peek_worktree_path(
        cls,
        project_dir: Path,
        task_id: str,
        *,
        state_dir: Path | None = None,
    ) -> Path | None:
        """The live worktree recorded for ``task_id``, or ``None`` — a pure read.

        ``load_for_task`` unlinks a state file whose worktree is gone; a caller
        that only wants to NAME the tree must not mutate lifecycle state as a
        side effect of looking. HATS-1540 wants it so the check runner can hand
        every gate ``AI_HATS_WORKTREE_PATH`` instead of each one re-deriving the
        state path and parsing the JSON by hand.
        """
        state_path = _resolve_state_dir(project_dir, state_dir, NOOP_LIFECYCLE) / (
            f"{_state_key(f'task/{task_id.lower()}')}.json"
        )
        try:
            raw = state_path.read_text()
        except FileNotFoundError:
            return None  # no record is an ANSWER: this task has no worktree
        # Every other failure is "cannot tell", which a caller must not read as
        # "no worktree" — that is how a gate waves through the tree it exists to
        # judge. OSError and JSONDecodeError both propagate.
        data = json.loads(raw)
        recorded = data.get("worktree_path")
        if not recorded:
            return None
        path = Path(recorded)
        return path if path.exists() else None

    @classmethod
    def peek_merged_sha(
        cls,
        project_dir: Path,
        task_id: str,
        *,
        state_dir: Path | None = None,
    ) -> str | None:
        """The commit this task's branch was merged as, or ``None`` — a pure read.

        The counterpart of :meth:`peek_worktree_path`, and the reason it exists:
        that one answers ``None`` both for a card that never had a worktree and
        for one whose worktree was merged and torn down. A gate cannot tell those
        apart, so it waved the second through (HATS-1664).
        """
        tomb = (
            _resolve_state_dir(project_dir, state_dir, NOOP_LIFECYCLE)
            / MERGED_SUBDIR
            / (f"{_state_key(f'task/{task_id.lower()}')}.json")
        )
        try:
            raw = tomb.read_text()
        except FileNotFoundError:
            return None  # no record is an ANSWER: nothing of this task was merged
        # Every other failure is "cannot tell" and propagates, exactly as above.
        return json.loads(raw).get("merge_sha") or None

    @classmethod
    def load_for_branch(
        cls,
        project_dir: Path,
        branch: str,
        *,
        lifecycle: WorktreeLifecycle = NOOP_LIFECYCLE,
        state_dir: Path | None = None,
    ) -> WorktreeManager | None:
        """Load worktree state by branch name."""
        key = _state_key(branch)
        return cls._load_by_key(project_dir, key, lifecycle=lifecycle, state_dir=state_dir)

    @classmethod
    def branch_exists(cls, project_dir: Path, branch: str, *, timeout: float | None = None) -> bool:
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
                timeout=timeout,
                env=scrubbed_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        # `git branch --list` exits 0 even when no match — empty stdout
        # is the "branch absent" signal.
        return bool(result.stdout.strip())

    @staticmethod
    def _git_probe(
        project_dir: Path, *args: str, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str] | None:
        """Run ``git <args>`` in ``project_dir`` (captured); None if git is
        absent or the probe exceeds ``timeout`` (HATS-1015 liveness budget)."""
        try:
            return subprocess.run(
                ["git", *args],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=scrubbed_git_env(),
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None

    @classmethod
    def _is_patch_integrated_probe(
        cls, project_dir: Path, branch: str, base: str, timeout: float | None = None
    ) -> bool:
        """Probe if all commits in ``branch`` are patch-equivalent on ``base`` (HATS-1370)."""
        res = cls._git_probe(project_dir, "cherry", base, branch, timeout=timeout)
        if res is None or res.returncode != 0:
            return False
        lines = res.stdout.splitlines()
        return not any(line.startswith("+") for line in lines)

    @classmethod
    def branch_merged_into_canonical_base(
        cls,
        project_dir: Path,
        branch: str,
        *,
        bases: tuple[str, ...] = CANONICAL_BASE_BRANCHES,
        timeout: float | None = None,
    ) -> str | None:
        """Return the canonical base ``branch`` is already merged into, else None.

        HATS-697: state-lost twin of the HATS-596 already-merged short-circuit.
        Resolves the base from the first existing of ``bases`` (injected for
        testability, R6) and tests ``git merge-base --is-ancestor``. ``None``
        on a genuine divergence, no canonical base, or an unreadable repo.
        HATS-1370: falls back to ``_is_patch_integrated_probe`` for rebased branches.
        """
        for base in bases:
            exists = cls._git_probe(
                project_dir, "rev-parse", "--verify", "--quiet", base, timeout=timeout
            )
            if exists is None or exists.returncode != 0:
                continue
            anc = cls._git_probe(
                project_dir, "merge-base", "--is-ancestor", branch, base, timeout=timeout
            )
            if anc is not None and anc.returncode == 0:
                return base
            if cls._is_patch_integrated_probe(project_dir, branch, base, timeout=timeout):
                return base
        return None

    @classmethod
    def delete_merged_branch(
        cls, project_dir: Path, branch: str, *, timeout: float | None = None
    ) -> bool:
        """Best-effort safe-delete (``git branch -d``) of an already-merged branch.

        HATS-697: clears the stale ``task/<id>`` ref after a state-lost
        finalize; ``-d`` refuses an un-merged branch, so failure is logged,
        never raised — finalize must not hinge on cleanup. True iff deleted.
        HATS-1370: if ``git branch -d`` fails on a rebased branch whose integration
        is confirmed by ``branch_merged_into_canonical_base``, falls back to ``git branch -D``.
        """
        res = cls._git_probe(project_dir, "branch", "-d", branch, timeout=timeout)
        if res is not None and res.returncode == 0:
            return True
        # HATS-1370: Fallback for rebased branches where git branch -d refuses unmerged SHAs
        base = cls.branch_merged_into_canonical_base(project_dir, branch, timeout=timeout)
        if base is not None:
            res_force = cls._git_probe(project_dir, "branch", "-D", branch, timeout=timeout)
            if res_force is not None and res_force.returncode == 0:
                return True
        detail = (res.stderr.strip() if res else "git unavailable") or "<no stderr>"
        logger.warning("Merged-branch cleanup skipped for '%s': %s", branch, detail)
        return False

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
    def _load_by_key(
        cls,
        project_dir: Path,
        key: str,
        *,
        lifecycle: WorktreeLifecycle = NOOP_LIFECYCLE,
        state_dir: Path | None = None,
        git_timeout: float | None = None,
    ) -> WorktreeManager | None:
        resolved_state_dir = _resolve_state_dir(project_dir, state_dir, lifecycle)
        state_path = resolved_state_dir / f"{key}.json"
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
        mgr = cls(
            project_dir,
            branch_name=data["branch"],
            lifecycle=lifecycle,
            state_dir=resolved_state_dir,
            git_timeout=git_timeout,
        )
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
    def list_active(
        cls,
        project_dir: Path,
        *,
        lifecycle: WorktreeLifecycle = NOOP_LIFECYCLE,
        state_dir: Path | None = None,
    ) -> list[WorktreeManager]:
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
        states_dir = _resolve_state_dir(project_dir, state_dir, lifecycle)
        if not states_dir.exists():
            return []
        result = []
        for f in sorted(states_dir.glob("*.json")):
            key = f.stem
            mgr = cls._load_by_key(project_dir, key, lifecycle=lifecycle, state_dir=states_dir)
            if mgr is not None and mgr._is_live_worktree():
                result.append(mgr)
        return result

    def _is_live_worktree(self) -> bool:
        """Whether git still backs this worktree (HATS-1205).

        ``_load_by_key`` drops an entry only when the directory itself is gone;
        a directory whose ``.git`` link file was removed (or that ``git worktree
        prune`` disowned) stayed "active" forever and kept padding the
        selector-ambiguity list. The ``.git`` link is the local, subprocess-free
        signal — a linked worktree always carries one.
        """
        return self.worktree_path is not None and (self.worktree_path / ".git").exists()

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
                env=scrubbed_git_env(),
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
                env=scrubbed_git_env(),
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

    @staticmethod
    def worktree_toplevel(path: Path) -> Path | None:
        """Return the linked worktree's OWN toplevel, or ``None``.

        The mirror image of :meth:`main_worktree_root`: where that hops to the
        MAIN checkout (via ``--git-common-dir``), this returns the linked
        worktree's own root (``--show-toplevel``) — the checkout the agent is
        actually editing. Returns ``None`` for the main worktree, a non-git
        path, or any git error (fail-safe).

        HATS-831: ``_project_dir`` hops a linked worktree to MAIN (HATS-524) to
        share the tracker, so the project-local ``libraries/`` layer would
        otherwise resolve to MAIN — invisible to worktree edits. The assembler
        re-points that one layer to this toplevel. Single ``git rev-parse``
        invocation, mirroring :meth:`is_inside_linked_worktree` (HATS-490).
        """
        try:
            result = subprocess.run(
                [
                    "git",
                    "rev-parse",
                    "--path-format=absolute",
                    "--show-toplevel",
                    "--git-dir",
                    "--git-common-dir",
                ],
                cwd=str(path),
                capture_output=True,
                text=True,
                check=True,
                env=scrubbed_git_env(),
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) != 3:
            return None
        toplevel, git_dir, common_dir = lines
        if Path(git_dir).resolve() == Path(common_dir).resolve():
            return None  # main worktree — caller keeps project_dir as-is
        return Path(toplevel).resolve()

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
                env=scrubbed_git_env(),
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
        # ``timeout`` is opt-in per call; when unset it falls back to the
        # manager's ``git_timeout`` liveness budget (HATS-1015, default None =
        # unbounded — behaviour unchanged for every existing caller). The
        # network ``fetch`` in :meth:`_check_drift` still pins its own
        # ``FETCH_TIMEOUT`` (HATS-711) explicitly.
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.project_dir),
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout if timeout is not None else self._git_timeout,
            env=scrubbed_git_env(),
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

    # --- HATS-1651: the merge point is atomic ------------------------------
    #
    # `git merge` is the one step here that can fail HALFWAY. Everything below
    # exists so that a failure is followed by a rollback, the rollback is
    # verified, and the refusal states the state that was OBSERVED.

    def _main_state(self) -> _MainState:
        """What the main checkout looks like right now, in the terms a rollback
        has to restore. **Call only while holding the base-branch lock.**

        Deliberately NOT a whole-porcelain snapshot to compare wholesale: on the
        FSM road rack writes the card's own tracker files into main while the
        merge runs, so an equality test over every entry would report those
        unrelated writes as merge leftovers. What a merge can change and nothing
        else does — the branch pointer, the staged set, unmerged entries — is
        what travels here.
        """
        staged = {}
        for line in self._git("status", "--porcelain").stdout.splitlines():
            if len(line) > 3 and line[0] not in " ?":
                staged[line[3:]] = line[:2]
        return _MainState(
            head=self._git("rev-parse", "HEAD").stdout.strip(),
            staged=staged,
            unmerged=self._unmerged_paths(),
        )

    def _unmerged_paths(self) -> tuple[str, ...]:
        """Paths git left with an unresolved merge, in the main checkout."""
        try:
            out = self._git("diff", "--name-only", "--diff-filter=U").stdout
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            # Not silent: the caller reports "git could not name them" rather
            # than an empty list, which would read as "there were none".
            logger.warning("could not list unmerged paths in %s: %s", self.project_dir, exc)
            return ()
        return tuple(path for path in out.splitlines() if path.strip())

    def _rollback_main_merge(self, before: _MainState) -> tuple[str, ...]:
        """Undo a half-finished merge in the main checkout; return what survived.

        An empty tuple means the pre-merge state was RE-OBSERVED, not that a
        command exited zero — the difference is the whole point of the class this
        feeds. Never ``reset --hard``: main may hold the operator's uncommitted
        work, and destroying it to tidy up a refusal would trade this card's bug
        for a worse one.
        """
        if self._main_repo_mid_merge():
            self._try_git("merge", "--abort")
        elif self._unmerged_paths():
            # `--squash` writes no MERGE_HEAD, so `merge --abort` has nothing to
            # work from; `reset --merge` is git's own recovery for that shape.
            self._try_git("reset", "--merge")
        return self._diverged(before)

    def _try_git(self, *args: str) -> None:
        """Run a recovery command, reporting rather than raising on failure —
        the verification below is what decides, and a raise here would replace an
        exact diagnosis with a stack trace."""
        try:
            self._git(*args)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.warning("`git %s` failed during merge rollback: %s", " ".join(args), exc)

    def _diverged(self, before: _MainState) -> tuple[str, ...]:
        """How the main checkout still differs from ``before``, in an operator's
        terms. Empty iff every fact the merge could have changed is back."""
        after = self._main_state()
        leftover: list[str] = []
        if self._main_repo_mid_merge():
            leftover.append(f"MERGE_HEAD is still present in {self.project_dir}")
        if after.head != before.head:
            leftover.append(
                f"HEAD moved from {self._short(before.head)} to {self._short(after.head)}"
            )
        for path in after.unmerged:
            if path not in before.unmerged:
                leftover.append(f"unresolved merge in {path}")
        for path, status in sorted(after.staged.items()):
            if before.staged.get(path) != status:
                leftover.append(f"staged change to {path} ({status.strip()})")
        return tuple(leftover)

    def _refuse_unmerged(self, before: _MainState, cause: Exception) -> NoReturn:
        """Roll the main checkout back, then refuse with the state observed after.

        The conflicted paths are read BEFORE the rollback — the rollback is what
        erases them, and they are what the operator has to go and resolve.
        """
        base = self._original_branch or "?"
        paths = self._unmerged_paths()
        leftover = self._rollback_main_merge(before)
        if leftover:
            raise WorktreeMergeLeftoverError(self.branch_name, base, leftover, paths) from cause
        if paths:
            raise WorktreeMergeConflictError(self.branch_name, base, paths) from cause
        # Nothing was left behind and nothing conflicted: git refused before it
        # mutated anything (an untracked-file collision, HATS-587). That failure
        # is already reported truthfully, so it keeps its own type.
        raise cause

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

    def _is_patch_integrated(self, branch: str, base: str) -> bool:
        """True iff all commits in ``branch`` are already patch-equivalent on ``base`` (HATS-1370).

        Uses ``git cherry <base> <branch>``. Output lines starting with '+' indicate
        commits NOT present on base. If no lines start with '+', returns True.
        """
        try:
            res = self._git("cherry", base, branch)
            lines = res.stdout.splitlines()
            return not any(line.startswith("+") for line in lines)
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    # ------------------------------------------------------------------
    # Drift detection (HATS-457 / HYP-017)
    # ------------------------------------------------------------------

    _DRIFT_PATH_LIMIT = 50  # max paths printed inline; overflow → "… N more"

    def _check_drift(self) -> None:
        """Raise WorktreeDriftError if the base moved and the branch lacks it.

        HATS-1307: drift needs BOTH terms. "Did it move since create" alone
        false-refuses a rebased branch; "is it contained in the branch" alone
        false-refuses the fork shape (base != merge_target, HATS-942), where
        the target is never an ancestor of the branch by design.

        Drift sources:
          * local: another worktree's `wt merge` advanced the local
            original branch and the worktree branch never took it in.
          * remote: someone pushed commits to ``origin/<base>`` that
            neither local nor the branch has — i.e. ``origin/<base>`` is
            an ancestor of neither. ``current_remote != current_local`` is
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

        # HATS-1307: moved AND not taken in. The second term spares a rebased
        # branch; the first spares the fork shape, whose target is never an
        # ancestor of the branch (HATS-942) yet has not changed under anyone.
        local_drifted = current_local != self._base_sha_at_create and not self._is_ancestor(
            current_local, self.branch_name
        )
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
            # HATS-1307: a branch rebased onto origin/<base> contains it too.
            and not self._is_ancestor(current_remote, self.branch_name)
        )

        if not local_drifted and not remote_drifted:
            return

        lines = [
            f"Worktree base '{self._original_branch}' drifted — branch "
            f"'{self.branch_name}' does not contain its latest commits."
        ]
        if local_drifted:
            n, paths = self._drift_summary(self.branch_name, current_local)
            lines.append(
                f"  local: {self._original_branch} ({self._short(current_local)}) is "
                f"{n} commit{'s' if n != 1 else ''} ahead of the branch's merge-base"
            )
            if paths:
                lines.append("  affected paths (local drift):")
                lines.extend(f"    {p}" for p in paths)
        if remote_drifted:
            assert current_remote is not None
            n_r, paths_r = self._drift_summary(self.branch_name, current_remote)
            lines.append(
                f"  remote: origin/{self._original_branch} is "
                f"{n_r} commit{'s' if n_r != 1 else ''} ahead of the branch"
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
        raise WorktreeDriftError(
            "\n".join(lines),
            branch_name=self.branch_name,
            base_branch=self._original_branch,
            worktree_path=self.worktree_path,
        )

    def _drift_summary(self, base: str, head: str) -> tuple[int, list[str]]:
        """Return (commit count, capped affected-path list) for base..head.

        HATS-1307: the path diff is three-dot (from the merge-base) so it
        lists only what ``head`` added — two-dot also reported ``base``-side
        work as a reversed change.
        """
        try:
            n_str = self._git("rev-list", "--count", f"{base}..{head}").stdout.strip()
            n = int(n_str) if n_str else 0
        except (subprocess.CalledProcessError, ValueError):
            n = 0
        try:
            diff = self._git("diff", "--name-only", f"{base}...{head}").stdout
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

        with _acquire_base_branch_lock(self._state_dir, self._original_branch):
            # HATS-602: authoritative mid-merge guard, inside the base lock.
            self._refuse_if_mid_merge()
            # HATS-1651: both steps under one snapshot. `--squash` leaves a
            # conflicted index and NO MERGE_HEAD, so a failure here is the one
            # shape the mid-merge guard above cannot catch on the next run.
            before = self._main_state()
            try:
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
            except subprocess.CalledProcessError as exc:
                self._refuse_unmerged(before, exc)
        logger.info("Squash-merged %s into %s", self.branch_name, self._original_branch)

    def _fast_forward_merge(self) -> None:
        """Merge worktree branch with --no-ff to preserve commit history.

        HATS-481 layered defense — see :meth:`_squash_merge` for details.
        """
        head_main = self._git("rev-parse", self._original_branch).stdout.strip()
        head_wt = self._git("rev-parse", self.branch_name).stdout.strip()
        if head_main == head_wt:
            return

        with _acquire_base_branch_lock(self._state_dir, self._original_branch):
            # HATS-602: authoritative mid-merge guard, inside the base lock.
            self._refuse_if_mid_merge()
            # HATS-1651: a conflict is the failure that leaves the main checkout
            # mid-merge; snapshot first so the refusal can state what it restored.
            before = self._main_state()
            try:
                _retry_git_merge(
                    self._git_with_ref_lock_wait,
                    "merge",
                    "--no-ff",
                    self.branch_name,
                    project_dir=self.project_dir,  # HATS-486 stale-lock probe
                )
            except subprocess.CalledProcessError as exc:
                self._refuse_unmerged(before, exc)
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
        * HATS-1332: a path git no longer tracks is already removed, not a
          failure — see :meth:`_clean_unregistered_shell`.
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
            if not self._is_registered_worktree(self.worktree_path):
                self._clean_unregistered_shell(self.worktree_path, force_rmtree=force_rmtree)
                return
            stderr = (exc.stderr or "").strip()
            tail = stderr.splitlines()[-1] if stderr else "<no stderr>"
            if not force_rmtree:
                raise WorktreeRemoveError(self.worktree_path, tail) from exc
            # Opt-in path: --force-remove was requested explicitly.
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

    def _is_registered_worktree(self, path: Path) -> bool:
        """True while git still carries an admin entry for ``path``.

        Probed positively rather than by matching git's stderr: the messages
        are gettext-translated and no caller pins a locale (HATS-1332).
        """
        target = path.resolve()
        return any(
            Path(entry["path"]).resolve() == target
            for entry in self.list_worktrees(self.project_dir)
            if "path" in entry
        )

    def _clean_unregistered_shell(self, path: Path, *, force_rmtree: bool) -> None:
        """Dispose of a leftover dir git no longer tracks (HATS-1332).

        Reached when ``git worktree remove`` failed only because the admin
        entry is gone — the removal's success condition. git is refusing
        nothing here, so the HATS-488/B-03 raise would defend nothing; but a
        shell that still holds files may hold work git can no longer see, so
        it is removed only when empty (the tmp-reaper shape) or on explicit
        explicit ``--force-remove``. Never raises: the caller's teardown has
        already succeeded.
        """
        holds_files = any(p.is_file() or p.is_symlink() for p in path.rglob("*"))
        if holds_files and not force_rmtree:
            logger.warning(
                "Worktree %s is no longer registered with git, but its dir still "
                "holds files — left on disk (rm -rf it, or `wt discard --force-remove`)",
                path,
            )
            return
        try:
            shutil.rmtree(path)  # safe-delete: ok unregistered shell (HATS-1332)
        except OSError as exc:
            logger.warning("Leftover worktree shell %s could not be removed: %s", path, exc)
            return
        logger.info("Worktree %s was already removed from git; leftover shell cleaned", path)

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
