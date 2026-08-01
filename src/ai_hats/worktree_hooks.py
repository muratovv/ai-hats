"""Worktree lifecycle hook execution (HATS-823, ADR-0012 D7).

Runs a single component-declared ``wt_in`` / ``wt_out`` script. Execution
mechanics moved to :mod:`ai_hats.hook_exec` (ADR-0020 D2, HATS-1151); what stays
here is this channel's own vocabulary: the timeout budget kept below the
lifecycle lock (HATS-711 class), the ``AI_HATS_*`` env it hands a hook, and the
stale-``library/wt-hooks/`` remedy in a missing-script reason. Policy — ``wt_out``
fail-closed vs ``wt_in`` warn-continue — remains the worktree manager's.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ai_hats_wt.locks import LIFECYCLE_LOCK_TIMEOUT
from .hook_exec import HookRun, HookVerdict, run_hook
from .paths import AI_HATS_PROJECT_DIR_ENV

# Default per-hook wall-clock budget. Strictly below LIFECYCLE_LOCK_TIMEOUT so
# the timeout — not the lock — is what bounds a hung hook (see module docstring).
WT_HOOK_TIMEOUT_S: float = 45.0
_TIMEOUT_ENV = "AI_HATS_WT_HOOK_TIMEOUT_S"

# D7: hook budget must stay under the lock timeout, else a hung hook makes a
# lock-waiting peer mis-blame a concurrent op. Explicit raise survives ``-O``.
if WT_HOOK_TIMEOUT_S >= LIFECYCLE_LOCK_TIMEOUT:  # pragma: no cover
    raise RuntimeError(
        f"WT_HOOK_TIMEOUT_S ({WT_HOOK_TIMEOUT_S}) must be < "
        f"LIFECYCLE_LOCK_TIMEOUT ({LIFECYCLE_LOCK_TIMEOUT})"
    )


def resolve_hook_timeout() -> float:
    """The effective per-hook timeout: ``AI_HATS_WT_HOOK_TIMEOUT_S`` or default.

    A missing / non-numeric / non-positive override falls back to the default
    (fail-safe — a typo must not disable the bound).
    """
    raw = os.environ.get(_TIMEOUT_ENV)
    if not raw:
        return WT_HOOK_TIMEOUT_S
    try:
        val = float(raw)
    except ValueError:
        return WT_HOOK_TIMEOUT_S
    return val if val > 0 else WT_HOOK_TIMEOUT_S


@dataclass(frozen=True)
class HookOutcome:
    """Result of one hook run. ``ok`` drives the caller's fail-closed decision."""

    ok: bool
    exit_code: int | None
    reason: str


def run_worktree_hook(
    script: Path,
    *,
    event: str,
    worktree_path: Path,
    project_dir: Path,
    branch_name: str,
    timeout: float | None = None,
    log_path: Path | None = None,
) -> HookOutcome:
    """Run one worktree hook ``script``; never raises on hook *failure*.

    ``KeyboardInterrupt`` (SIGINT) is intentionally allowed to propagate so an
    operator can abort a teardown. Every non-pass outcome — refuse, broke and
    corrupt alike — comes back as ``ok=False``, so a ``wt_out`` gate cannot fail
    open on a hook that merely failed to start.
    """
    run = run_hook(
        script,
        timeout=resolve_hook_timeout() if timeout is None else timeout,
        project_dir=project_dir,
        env={
            **os.environ,
            "AI_HATS_WORKTREE_PATH": str(worktree_path),
            AI_HATS_PROJECT_DIR_ENV: str(project_dir),
            "AI_HATS_BRANCH_NAME": branch_name,
            "AI_HATS_EVENT": event,
        },
        log_path=log_path,
    )
    if run.ok:
        return HookOutcome(True, run.exit_code, "ok")
    return HookOutcome(False, run.exit_code, _wt_reason(run, script))


def _wt_reason(run: HookRun, script: Path) -> str:
    """HATS-833: a vanished managed script means the parent's ``library/wt-hooks/``
    is stale, so name the remedy rather than only the symptom."""
    if run.verdict is HookVerdict.CORRUPT and not script.is_file():
        return f"{run.reason} — run 'ai-hats self init' to re-materialize"
    return run.reason
