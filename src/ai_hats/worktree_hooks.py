"""Worktree lifecycle hook execution (HATS-823, ADR-0012 D7).

Runs a single component-declared ``wt_in`` / ``wt_out`` script. Execution
mechanics moved to :mod:`ai_hats.hook_exec` (ADR-0020 D2, HATS-1151); what stays
here is this channel's own vocabulary: the timeout budget kept below the
lifecycle lock (HATS-711 class), the ``AI_HATS_*`` env it hands a hook, and what
a missing script means now that hooks spawn in place. Policy — ``wt_out``
fail-closed vs ``wt_in`` warn-continue — remains the worktree manager's.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.deadline import Deadline

from .env import WT_HOOK_TIMEOUT, read_budget
from .hook_exec import HookOutcomeKind, HookRun, run_hook

# What this channel ASKS for. HATS-1593: it is a request, not the timeout — the
# lock the caller holds mints the ceiling and `run_hook` takes the smaller of
# the two. A constant here cannot know which of four locks is held above it.
WT_HOOK_TIMEOUT_S: float = WT_HOOK_TIMEOUT.default
_TIMEOUT_ENV = WT_HOOK_TIMEOUT.name


def resolve_hook_timeout() -> float:
    """The effective per-hook timeout: ``AI_HATS_WT_HOOK_TIMEOUT_S`` or default.

    A missing / non-numeric / non-positive override falls back to the default
    (fail-safe — a typo must not disable the bound).
    """
    return read_budget(WT_HOOK_TIMEOUT)


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
    deadline: Deadline,
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
        point=_wt_point(event),
        budget=resolve_hook_timeout() if timeout is None else timeout,
        deadline=deadline,
        project_dir=project_dir,
        worktree_path=worktree_path,
        # This channel's own vocabulary, kept verbatim: `AI_HATS_EVENT` has live
        # readers outside this repo, and renaming it is HATS-1142's migration.
        extra_env={"AI_HATS_BRANCH_NAME": branch_name, "AI_HATS_EVENT": event},
        log_path=log_path,
    )
    if run.ok:
        return HookOutcome(True, run.exit_code, "ok")
    return HookOutcome(False, run.exit_code, _wt_reason(run, script))


def _wt_point(event: str) -> str:
    """This channel's event → the catalog's fully-qualified point name
    (``check_points._static_points``), which is what the shared env carries."""
    return "wt:create" if event == "wt_in" else f"wt:teardown[{event}]"


def _wt_reason(run: HookRun, script: Path) -> str:
    """HATS-1269: scripts spawn in place, so a missing one means the declaring
    skill stopped shipping it — say that, not a re-materialize step that no
    longer exists. The run already knows which failure it was, so this no longer
    re-stats the file to find out (HATS-1572)."""
    if run.kind is HookOutcomeKind.SCRIPT_MISSING:
        return f"{run.reason} — the declaring skill no longer ships this script"
    return run.reason
