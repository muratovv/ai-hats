"""The ``ai-hats:startup`` point — a declared gate that runs before the session.

HATS-1581 (ADR-0019 D11, epic HATS-1138). ai-hats fires this one itself, so the
row's cargo is validated at composition (``check_points._OWNED_POINTS``) and the
bytes are resolved the way every other bound check resolves them.

Fail-closed on purpose: a gate whose substrate is broken refuses the launch
rather than warning past it, because "the gate could not run" and "the gate
passed" must never look the same — that silence is what the epic exists to
remove. ``on_error: warn`` softens only a check that RAN and broke, never one
whose script is missing or unexecutable (``HookRun.downgradable``).
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from typing import TYPE_CHECKING, Callable

from .check_points import AI_HATS_APP, STARTUP_POINT, check_log_name
from .startup_notices import StartupNotice, show_fatal_notice_and_exit

if TYPE_CHECKING:  # pragma: no cover — typing only
    from ai_hats_core import CompositionResult, ResolvedCheck

    from .session_identity import SessionIdentity

#: Exit code for a launch a startup gate refused. Deliberately none of the codes
#: already spoken for: 1 is any generic failure, 2 is click's UsageError (a
#: malformed command line), and 130 is the SIGINT default preset before the
#: spawn — reusing it would make a refused gate read as an operator Ctrl-C.
STARTUP_REFUSED_EXIT = 3


def run_startup_checks(
    project_dir: Path,
    *,
    session_dir: Path,
    identity: SessionIdentity | None = None,
    extra_env: Mapping[str, str] | None = None,
    compose: Callable[[Path], CompositionResult | None] | None = None,
) -> list[StartupNotice]:
    """Run every row bound to ``ai-hats:startup``; return the notices to surface.

    Never returns on a refusal — it terminates through
    :func:`~ai_hats.startup_notices.show_fatal_notice_and_exit`, so the caller
    cannot accidentally launch past one.

    ``extra_env`` is the session's own environment delta, and passing it is not
    optional dressing: a gate judging a DIFFERENT environment than the session
    will get is the "green at startup, red at the first transition" split this
    point exists to close.

    ``compose`` is the same argument in composition form, and the caller here is
    in-process: it hands over the composition the session was built from rather
    than letting the channel compose a second one. It went unpassed through
    HATS-1594, which is how a launch died on a gate belonging to another role.
    """  # comment-length: allow — both arguments exist to stop the same split
    from .check_resolve import CheckResolutionError, resolve_checks_at

    try:
        checks = resolve_checks_at(
            project_dir, AI_HATS_APP, STARTUP_POINT, identity=identity, compose=compose
        )
    except CheckResolutionError as exc:
        # Resolution failing is not "nothing is declared": a row may exist and be
        # unreadable, and launching then would arm nothing while looking armed.
        _refuse(f"checks: {exc}", session_dir)

    notices: list[StartupNotice] = []
    for check in checks:
        run = _run_one(check, project_dir, session_dir, extra_env)
        if run.ok:
            continue
        reason = _reason(check, run)
        if check.on_error == "warn" and run.downgradable:
            notices.append(StartupNotice("warn", f"{reason}; downgraded by on_error: warn"))
            continue
        _refuse(reason, session_dir)
    return notices


def _run_one(
    check: ResolvedCheck,
    project_dir: Path,
    session_dir: Path,
    extra_env: Mapping[str, str] | None,
):
    from ai_hats_core.deadline import Deadline

    from .hook_exec import run_hook
    from .worktree_hooks import resolve_hook_timeout

    budget = resolve_hook_timeout()
    return run_hook(
        check.script_path,
        point=STARTUP_POINT,
        budget=budget,
        # Nothing serialises session startup, so this budget IS the ceiling.
        deadline=Deadline.without_lock(budget, why="session startup"),
        project_dir=project_dir,
        extra_env=dict(extra_env or {}),
        # The dedup identity, not the basename: run_hook truncates the log it is
        # handed, so a coarser name lets one row wipe another's (HATS-1137).
        log_path=session_dir / "checks" / check_log_name(STARTUP_POINT, check),
    )


def _reason(check: ResolvedCheck, run) -> str:
    """A refusal that spoke stands alone; anything else names the binding.

    Same shape as ``wt_lifecycle._check_refusal`` and ``rack_consumers._refusal``
    — one script bound at several points must read the same on each.
    """
    from .hook_exec import HookVerdict

    if run.verdict is HookVerdict.REFUSE:
        return run.reason
    return f"checks: {check.declared_by!r} binds {check.run} under apps.{check.app} — {run.reason}"


def _refuse(reason: str, session_dir: Path):
    show_fatal_notice_and_exit(
        f"a startup gate refused this session at {AI_HATS_APP}:{STARTUP_POINT} — "
        f"nothing was launched.\n{reason}",
        exit_code=STARTUP_REFUSED_EXIT,
        session_dir=session_dir,
    )


__all__ = ["STARTUP_REFUSED_EXIT", "run_startup_checks"]
