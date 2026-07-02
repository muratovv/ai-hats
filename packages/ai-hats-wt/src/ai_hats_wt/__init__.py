"""Hook-agnostic git-worktree engine (ADR-0013, the ``wt`` core).

This package is the self-contained worktree engine: :class:`WorktreeManager`
(create / merge / discard / cleanup + the static git probes), the L1–L4
concurrency model (:mod:`ai_hats_wt.locks`, ADR-0006), the ``IsolationMode``
enum, the typed exceptions, and the :class:`WorktreeLifecycle` extension-point
protocol (default :data:`NOOP_LIFECYCLE`, so a bare core runs no hooks).

The one-directional import rule (ADR-0013 D6) forbids anything here from importing
ai-hats accretions (``paths`` / ``models`` / ``assembler`` / ``composer`` /
``materialize`` / ``state`` / ``worktree_hooks``); ai-hats imports *from* this
package, never the reverse. ``__all__`` below is the deliberate D9 public surface a
standalone consumer drives — manager-internal constants (e.g.
``CANONICAL_BASE_BRANCHES``) and lock internals stay submodule-only on purpose.
"""

from __future__ import annotations

from .locks import WorktreeLockError
from .manager import (
    IsolationMode,
    LifecycleContext,
    NOOP_LIFECYCLE,
    OriginalBranchMissingError,
    WorktreeBaseBranchError,
    WorktreeBaseBranchMismatchError,
    WorktreeCreateError,
    WorktreeDriftError,
    WorktreeDirtyError,
    WorktreeLifecycle,
    WorktreeMainRepoMidMergeError,
    WorktreeManager,
    WorktreePartialCleanupError,
    WorktreeRemoveError,
    WorktreeStateIncompleteError,
    WorktreeStateLostError,
    WorktreeTeardownAborted,
    assert_head_is_canonical_base,
)

__all__ = [
    # Engine + git probes
    "WorktreeManager",
    "IsolationMode",
    "assert_head_is_canonical_base",
    # Lifecycle extension-point (ADR-0013 D2)
    "WorktreeLifecycle",
    "LifecycleContext",
    "NOOP_LIFECYCLE",
    # Typed exceptions (the clean seam, ADR-0013 D1)
    "WorktreeDirtyError",
    "WorktreeCreateError",
    "WorktreePartialCleanupError",
    "WorktreeRemoveError",
    "OriginalBranchMissingError",
    "WorktreeStateLostError",
    "WorktreeStateIncompleteError",
    "WorktreeDriftError",
    "WorktreeBaseBranchError",
    "WorktreeBaseBranchMismatchError",
    "WorktreeMainRepoMidMergeError",
    "WorktreeTeardownAborted",
    "WorktreeLockError",
]
