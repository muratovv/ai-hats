"""ai-hats worktree lifecycle bundle — the hook-running extension-point impl.

ADR-0013 P1 / HATS-849. The worktree *core* (:mod:`ai_hats_wt`) is
hook-agnostic: it fires ``on_created`` / ``before_teardown`` extension-points at
each lifecycle site and owns the per-route teardown control-flow, but knows
nothing about hooks. THIS module is the ai-hats accretion that plugs in: it
decides *what* runs at those points (the component-declared ``wt_in`` / ``wt_out``
scripts via :func:`ai_hats.worktree_hooks.run_worktree_hook`), the fail-vs-warn
policy, the ``skip_hooks`` escape, and the legacy warn-not-drop. ai-hats injects
:data:`HOOK_LIFECYCLE` at every ``WorktreeManager`` construction / load; a bare
core keeps the no-op default and runs no hooks.

Policy (ADR-0012 D3/D7, relocated here from the engine):

- ``wt_in`` (``on_created``) — **warn-continue**: a create-time failure is
  friction, not data loss; it is logged and never raises.
- ``wt_out`` (``before_teardown``) — **fail-closed**: a hook failure raises the
  core-owned :class:`ai_hats_wt.WorktreeTeardownAborted` (with the
  :class:`WorktreeHookError` riding as ``__cause__``), aborting teardown and
  preserving the worktree. The core then propagates (merge/discard) or
  suppresses (cleanup) per route.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NoReturn

from .check_points import WT_APP, check_failure_reason, check_log_token
from .hook_exec import run_hook
from .worktree_hooks import resolve_hook_timeout, run_worktree_hook
from ai_hats_wt import (
    WT_TEARDOWN_EVENTS,
    LifecycleContext,
    WorktreeMergeAborted,
    WorktreeTeardownAborted,
)
from ai_hats_wt.locks import _state_key

logger = logging.getLogger(__name__)

#: The point ``merge()`` fires, in the ``wt`` app's own grammar (HATS-1545).
WT_PRE_MERGE = "pre-merge"


class WorktreeHookError(Exception):
    """A ``wt_out`` lifecycle hook failed and teardown is fail-closed (HATS-823).

    Relocated from the worktree engine by ADR-0013 D8: this is *hook
    vocabulary*, so it belongs with the lifted hook layer, not the hook-agnostic
    core. It never escapes this module as the raised type — it rides as the
    ``__cause__`` of the core :class:`ai_hats_wt.WorktreeTeardownAborted`,
    which the CLI surfaces via ``str(e.__cause__)``.
    """


def _wt_hook_log_dir(state_dir, branch_name: str):
    # ADR-0013 D4 / HATS-851: resolve hook-logs off the manager's INJECTED
    # state-dir base (ctx.state_dir), not a recomputed worktrees_dir(project_dir),
    # so state + hook-logs stay co-located even under a custom-base driver.
    return state_dir / f"{_state_key(branch_name)}.logs"


def _skill_search_roots(project_dir: Path, worktree_path: Path | None) -> list[Path]:
    """Library roots to look a carry row's declaring skill up in (HATS-1269).

    The worktree's own ``libraries/`` ranks highest: composition *inside* a
    worktree re-points the project-local layer to it (HATS-831), so that copy is
    what create saw — while teardown runs from the main checkout, where it would
    otherwise be invisible.
    """
    from .library_paths import build_library_paths
    from .models import ProjectConfig
    from .paths import PROJECT_CONFIG
    from .paths.constants import LIBRARIES_DIRNAME

    try:
        configured = list(ProjectConfig.from_yaml(project_dir / PROJECT_CONFIG).library_paths)
    except Exception as exc:  # noqa: BLE001 — a bad config must not decide hook policy
        logger.warning(
            "worktree hooks: could not read %s (%s) — resolving hook scripts "
            "without its library_paths",
            PROJECT_CONFIG,
            exc,
        )
        configured = []
    extra: list[Path] = []
    if worktree_path is not None and (worktree_path / LIBRARIES_DIRNAME).is_dir():
        extra.append(worktree_path / LIBRARIES_DIRNAME)
    return build_library_paths(project_dir, config_paths=configured, extra=extra)


def resolve_hook_script(
    project_dir: Path, row: dict, *, worktree_path: Path | None = None
) -> tuple[Path | None, str]:
    """A carry row's script, resolved fresh inside its declaring skill dir.

    Returns ``(path, "")`` or ``(None, reason)``. ADR-0020 D1: the path is
    recomputed at every spawn, never persisted — downstream the library lives in
    a versioned venv that ``self update`` replaces. Both halves of the row are
    persisted state a tamperer can reach, so the resolved path is contained
    against its skill root (M11).
    """
    from .library_paths import find_component_dir
    from .models import resolve_namespace

    skill = str(row.get("skill", ""))
    script = str(row.get("script", ""))
    if not skill or not script:
        return None, f"carry row is incomplete (skill={skill!r}, script={script!r})"
    if not _is_plain_name(skill):
        return None, f"skill {skill!r} is not a plain component name"

    roots = _skill_search_roots(project_dir, worktree_path)
    skill_dir = find_component_dir(roots, "skills", resolve_namespace(skill))
    if skill_dir is None:
        return None, f"skill {skill!r} declaring this hook is not in the library"
    skill_dir = skill_dir.resolve()

    candidate = (skill_dir / script).resolve()
    if not _contains(skill_dir, candidate):
        return None, f"script {script!r} escapes the root of skill {skill!r}"
    return candidate, ""


def _is_plain_name(skill: str) -> bool:
    """A component name, never a path. ``dev::python`` is legal (namespaces map
    to a subdir); ``..`` and an absolute root are not — a traversal that lands
    back inside a library root would still name a directory nobody declared."""
    from .models import resolve_namespace

    as_path = Path(resolve_namespace(skill))
    return bool(as_path.parts) and ".." not in as_path.parts and not as_path.is_absolute()


def _contains(root: Path, candidate: Path) -> bool:
    try:
        return candidate.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


class HookRunningLifecycle:
    """Runs component-declared ``wt_in`` / ``wt_out`` hooks at the core lifecycle
    sites. Stateless — every input comes from the :class:`LifecycleContext`, so a
    single module-level instance (:data:`HOOK_LIFECYCLE`) serves all managers."""

    def on_created(self, ctx: LifecycleContext) -> None:
        """Run ``wt_in`` hooks after ``git worktree add`` (warn-and-continue).

        A create-time hook failure is friction, not data loss (ADR-0012 D3/D7):
        logged and skipped, never aborting worktree creation.
        """
        rows = ctx.carry.get("wt_in") or []
        if not rows or ctx.worktree_path is None:
            return
        log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
        for row in rows:
            script, why = resolve_hook_script(ctx.project_dir, row, worktree_path=ctx.worktree_path)
            if script is None:
                _warn_wt_in_failed(row, why)
                continue
            outcome = run_worktree_hook(
                script,
                event="wt_in",
                worktree_path=ctx.worktree_path,
                project_dir=ctx.project_dir,
                branch_name=ctx.branch_name,
                deadline=ctx.deadline,
                log_path=log_dir / f"wt_in-{script.name}.log",
            )
            if not outcome.ok:
                _warn_wt_in_failed(row, outcome.reason)

    def before_merge(self, ctx: LifecycleContext) -> None:
        """Run every ``wt:pre-merge`` check before the merge mutates anything.

        Resolved LIVE from the composition, not from the create-time carry: a
        gate must judge by the rule in force at merge time, and the carry is the
        record of what a worktree holds (ADR-0013 D5), not of what may guard it.
        The catalog fixes this point's policy at ``refuse`` (ADR-0019 D4), so
        every non-pass outcome — refusal, broken script, missing script alike —
        aborts. ``skip_hooks`` does NOT apply: it exists so a teardown can drop
        data harvesting, never so a merge can drop its gate.
        """  # comment-length: allow — the three deliberate non-symmetries with wt_out
        if ctx.worktree_path is None:
            return
        from .check_resolve import CheckResolutionError, resolve_checks_at, session_identity_for

        try:
            checks = resolve_checks_at(
                ctx.project_dir,
                WT_APP,
                WT_PRE_MERGE,
                # Unscoped, another project's session chose the bindings (HATS-1631).
                identity=session_identity_for(ctx.project_dir),
            )
        except CheckResolutionError as exc:
            _raise_merge_aborted(ctx.branch_name, f"checks: {exc}")
        if not checks:
            return
        log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
        for check in checks:
            run = run_hook(
                check.script_path,
                point=WT_PRE_MERGE,
                budget=resolve_hook_timeout(),
                deadline=ctx.deadline,
                project_dir=ctx.project_dir,
                worktree_path=ctx.worktree_path,
                extra_env={"AI_HATS_BRANCH_NAME": ctx.branch_name},
                # The dedup identity, not the basename: two rows whose scripts
                # share a basename would otherwise truncate each other's log
                # while the first one's reason still points at it — the HATS-1137
                # defect `rack_consumers._escaped` exists to prevent.
                log_path=log_dir / f"pre-merge~{check_log_token(check)}.log",
            )
            if not run.ok:
                _raise_merge_aborted(
                    ctx.branch_name,
                    check_failure_reason(check, run),
                )

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        """Run ``wt_out`` hooks bound to ``event`` before the core removes the dir.

        Fail-closed: a hook failure raises
        :class:`ai_hats_wt.WorktreeTeardownAborted` (with a
        :class:`WorktreeHookError` ``__cause__``); the core aborts teardown and
        preserves the worktree. ``ctx.skip_hooks`` is the conscious escape; a
        legacy (carry-less) state warns but does not drop.
        """
        if ctx.legacy:
            # Pre-upgrade worktree: can't know what it holds → warn, don't drop.
            logger.warning(
                "Worktree '%s' predates wt-hooks (no carry recorded at create) "
                "— gitignored data cannot be auto-harvested on %s; back it up "
                "manually if needed.",
                ctx.branch_name,
                event,
            )
        rows = [
            r
            for r in (ctx.carry.get("wt_out") or [])
            if event in (r.get("on") or WT_TEARDOWN_EVENTS)
        ]
        if ctx.worktree_path is None or not rows:
            return
        if ctx.skip_hooks:
            logger.warning(
                "wt_out hooks SKIPPED for '%s' on %s via --skip-hooks — "
                "unharvested gitignored data will be destroyed (%d hook(s))",
                ctx.branch_name,
                event,
                len(rows),
            )
            return
        log_dir = _wt_hook_log_dir(ctx.state_dir, ctx.branch_name)
        for row in rows:
            script, why = resolve_hook_script(ctx.project_dir, row, worktree_path=ctx.worktree_path)
            if script is None:
                _raise_teardown_aborted(event, ctx.branch_name, row, why)
            outcome = run_worktree_hook(
                script,
                event=event,
                worktree_path=ctx.worktree_path,
                project_dir=ctx.project_dir,
                branch_name=ctx.branch_name,
                deadline=ctx.deadline,
                log_path=log_dir / f"{event}-{script.name}.log",
            )
            if not outcome.ok:
                _raise_teardown_aborted(event, ctx.branch_name, row, outcome.reason)


def _warn_wt_in_failed(row: dict, reason: str) -> None:
    logger.warning(
        "wt_in hook from skill '%s' failed — continuing (create-time friction, not data loss): %s",
        row.get("skill", "?"),
        reason,
    )


def _raise_teardown_aborted(event: str, branch_name: str, row: dict, reason: str) -> NoReturn:
    """Raise the core abort wrapping a :class:`WorktreeHookError` cause (D8).

    The ``__cause__`` carries the full recovery recipe. On the propagated
    merge/discard routes the CLI surfaces ``str(e.__cause__)`` and the FSM
    surfaces ``str(exc)`` — so the abort message there is kept identical to the
    cause (rich on both). The ``cleanup`` route SUPPRESSES the abort and logs
    only ``str(exc)`` (the cause is never surfaced), so its abort message must
    itself name the sub-agent recovery (D8 provenance).
    """
    skill = row.get("skill", "?")
    match event:
        case "merge":
            subcmd = "merge"
        case "discard" | "cleanup":
            subcmd = "discard"
        case _:
            raise ValueError(f"Unknown wt teardown event: {event!r}")

    cmd_hint = f"ai-hats wt {subcmd} {branch_name} --skip-hooks"

    detail = (
        f"wt_out hook from skill '{skill}' failed on {event} ({reason}). "
        f"Teardown aborted — worktree '{branch_name}' preserved. Fix the hook "
        f"and retry, or force with `{cmd_hint}` and repeat the command (accepts the data loss)."
    )
    cause = WorktreeHookError(detail)
    if event == "cleanup":
        message = (
            f"wt_out hook failed on cleanup — recover with "
            f"`ai-hats wt discard {branch_name} --skip-hooks`: {reason}"
        )
    else:
        message = detail
    raise WorktreeTeardownAborted(message) from cause


def _raise_merge_aborted(branch_name: str, reason: str) -> NoReturn:
    """Refuse the merge with the tree intact (HATS-1540).

    No ``--skip-hooks`` recipe, unlike its teardown sibling: that escape exists
    to accept losing harvested data, and there is no equivalent thing to accept
    here — the way past this gate is to satisfy it.
    """
    raise WorktreeMergeAborted(
        f"checks refused the merge of worktree '{branch_name}' at {WT_PRE_MERGE} — "
        f"nothing was merged and the worktree is intact.\n{reason}"
    )


#: The bundle ai-hats injects at every WorktreeManager construction / load.
HOOK_LIFECYCLE: HookRunningLifecycle = HookRunningLifecycle()
