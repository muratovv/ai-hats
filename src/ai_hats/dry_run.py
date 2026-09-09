"""``--dry-run``: what a launch would deliver, without delivering it (HATS-1211).

Runs the real assembly with a ``PlanMaterializer``, so the report is the session
minus the spawn — not a reconstruction of it. Nothing is written: no session
record, no worktree, no ownership hold, no audit.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from .check_snapshot import describe_checks
from .consent_wrapper import materialize_consent_wrappers
from .materialization import ApplyMaterializer, Materializer, PlanMaterializer
from .session_artifacts import (
    AT_LAUNCH,
    BuiltArtifacts,
    RunMode,
    SessionPolicy,
    assemble_launch_command,
    assemble_launch_env,
)
from .session_report import SessionReport

# A real sid is minted by the session manager, which a dry-run must not touch.
# Fixed so reported paths are stable and diffable.
DRY_RUN_SESSION_ID = "dry-run"
DRY_RUN_MATERIALIZE_SESSION_ID = "dry-run-materialize"

__all__ = [
    "AT_LAUNCH",
    "DRY_RUN_MATERIALIZE_SESSION_ID",
    "DRY_RUN_SESSION_ID",
    "dry_run_automate",
    "dry_run_hitl",
]


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()} if root.is_dir() else set()


def _detect_escapes(cache_dir: Path, before: set[Path]) -> tuple[Path, ...]:
    """Files that appeared during a plan-mode build went around the port.

    The dry-run cache dir is ours alone (a synthetic sid), so removing what
    escaped is safe — and it keeps the promise that a dry-run writes nothing.
    """
    import shutil

    escaped = tuple(sorted(_files_under(cache_dir) - before))
    if escaped and cache_dir.is_dir():
        shutil.rmtree(cache_dir, ignore_errors=True)  # safe-delete: ok synthetic dry-run sid
    return escaped


@contextlib.contextmanager
def _exclusive_rebuild(cache_dir: Path, port: Materializer, *, materialize: bool):
    """Serialise the wipe-and-rebuild of the session-cache dir.

    Only ``--materialize`` writes, and its sid is FIXED, so two concurrent runs
    share one directory. That is the multi-writer case HATS-1248 argued away for
    a sid-keyed dir — a fixed sid brings it back, and without this a peer's
    ``rmtree`` lands in the middle of our build (HATS-1551 review).

    The lock sits BESIDE the target, never inside it: the rebuild begins by
    removing the directory (HATS-604's reason, same shape). A plan-mode port
    locks nothing, because it writes nothing.
    """  # comment-length: allow — why a fixed sid needs a lock at all
    with port.lock(cache_dir.parent / f"{cache_dir.name}.lock"):
        if materialize and cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)  # safe-delete: ok synthetic sid
        yield


def dry_run_hitl(
    layout: ProjectLayout,
    *,
    role: str | None = None,
    provider: str | None = None,
    extra_args: list[str] | None = None,
    policy: SessionPolicy | None = None,
    materialize: bool = False,
) -> SessionReport:
    """Build the HITL session in plan mode and report it."""
    project_dir = layout.root
    from .composition_seam import build_preview_payload

    # The seam's read-only payload: same compose facade, no ``set_role`` write.
    payload = build_preview_payload(project_dir, role=role, provider=provider)
    prov = payload.provider
    eff_policy = policy or SessionPolicy()

    sid = DRY_RUN_MATERIALIZE_SESSION_ID if materialize else DRY_RUN_SESSION_ID
    cache_dir = layout.cache.session(sid)
    port = ApplyMaterializer() if materialize else PlanMaterializer()
    artifacts = BuiltArtifacts(port=port)
    with _exclusive_rebuild(cache_dir, port, materialize=materialize):
        before = _files_under(cache_dir)
        with prov.execution_context(layout):
            prov.build_session_artifacts(
                layout,
                payload.result,
                sid,
                run_mode=RunMode.HITL,
                policy=eff_policy,
                artifacts=artifacts,
            )
            if prov.supports_session_command_wrappers():
                materialize_consent_wrappers(layout, payload.result, sid, prov, artifacts)

    env = assemble_launch_env(
        prov,
        layout,
        cache_dir,
        session_id=sid,
        trace_path=AT_LAUNCH,
        role=payload.role_expression,
        root_pid=AT_LAUNCH,
        extra_env=artifacts.extra_env,
        run_mode=RunMode.HITL,
        claim=False,
    )
    launch = assemble_launch_command(
        prov,
        extra_args=extra_args,
        session_args=artifacts.cli_args,
        provider_session_id=AT_LAUNCH,
    )
    prompt = next((p for p in artifacts.materialized if p.suffix in (".md", ".MD")), None)
    checks, check_notes = describe_checks(prov, layout, payload.result, sid, artifacts.port.plan)
    notes = [*check_notes, *_launch_notices(prov, layout, payload.result, eff_policy)]
    if materialize:
        notes.append(f"materialized session tree written to disk at {cache_dir}")
        notes.append(
            f"session tree uses synthetic session_id '{sid}'; real sessions mint their own sid"
        )
    escapes = () if materialize else _detect_escapes(cache_dir, before)
    return SessionReport(
        role=payload.effective_role,
        provider=prov.name,
        run_mode=RunMode.HITL.value,
        policy=eff_policy,
        launch=launch,
        env=env,
        prompt=prompt,
        plan=artifacts.port.plan,
        cwd=str(project_dir),
        escapes=escapes,
        checks=checks,
        consent=payload.result.consent,
        notes=tuple(notes),
        prompt_text=artifacts.full_content,
    )


def _launch_notices(prov, layout: ProjectLayout, result, policy: SessionPolicy) -> list[str]:
    """What the runner would say at startup about THIS surface (HATS-1548).

    A dry-run that stays quiet where the launch warns is the same silence the
    notices exist to remove — the operator learns it one session too late.
    """
    from .check_snapshot import legacy_launch_notices, surface_skew_notice

    notices = []
    if result.consent and not prov.supports_session_command_wrappers():
        notices.append(
            f"provider {prov.name!r} cannot enforce role-declared command consent; "
            "the real HITL launch will be refused"
        )
    if not prov.handles_artifact_categories():
        return [*notices, *legacy_launch_notices(prov.name, result, policy)]
    skew = surface_skew_notice(prov.name, prov, layout, result)
    return [*notices, *([skew] if skew else [])]


def dry_run_automate(
    layout: ProjectLayout,
    *,
    role: str | None = None,
    provider: str | None = None,
    task: str = "",
    ticket_id: str = "",
    model: str = "",
    policy: SessionPolicy | None = None,
    materialize: bool = False,
) -> SessionReport:
    """Build the sub-agent session in plan mode and report it.

    Follows the runner's own AUTOMATE assembly rather than a tidier one, so the
    report shows what a sub-agent really gets — including, today, the paths that
    go around the port (see ``escapes``).
    """
    project_dir = layout.root
    from .composition_seam import build_preview_payload

    # The seam's read-only payload: same compose facade, no ``set_role`` write.
    payload = build_preview_payload(project_dir, role=role, provider=provider)
    prov = payload.provider
    eff_policy = policy or SessionPolicy()

    sid = DRY_RUN_MATERIALIZE_SESSION_ID if materialize else DRY_RUN_SESSION_ID
    cache_dir = layout.cache.session(sid)
    port = ApplyMaterializer() if materialize else PlanMaterializer()
    artifacts = BuiltArtifacts(port=port)
    with _exclusive_rebuild(cache_dir, port, materialize=materialize):
        before = _files_under(cache_dir)
        with prov.execution_context(layout):
            prov.build_session_artifacts(
                layout,
                payload.result,
                sid,
                run_mode=RunMode.AUTOMATE,
                policy=eff_policy,
                artifacts=artifacts,
            )

    checks, check_notes = describe_checks(prov, layout, payload.result, sid, artifacts.port.plan)
    env = assemble_launch_env(
        prov,
        layout,
        cache_dir,
        session_id=sid,
        trace_path=AT_LAUNCH,
        role=payload.role_expression,
        root_pid=AT_LAUNCH,
        extra_env=artifacts.extra_env,
        run_mode=RunMode.AUTOMATE,
        claim=False,
    )
    described = prov.describe_automate_launch(
        layout,
        payload.result,
        sid,
        artifacts,
        task=task,
        ticket_id=ticket_id,
        model=model,
        env=env,
    )

    notes = list(check_notes)
    if materialize:
        notes.append(f"materialized session tree written to disk at {cache_dir}")
        notes.append(
            f"session tree uses synthetic session_id '{sid}'; real sessions mint their own sid"
        )
    escapes = () if materialize else _detect_escapes(cache_dir, before)
    return SessionReport(
        role=payload.effective_role,
        provider=prov.name,
        run_mode=RunMode.AUTOMATE.value,
        policy=eff_policy,
        launch=described.launch,
        env=env,
        prompt=next((p for p in artifacts.materialized if p.suffix == ".md"), None),
        plan=artifacts.port.plan,
        cwd="<worktree, assigned at launch>",
        escapes=escapes,
        notes=tuple(notes),
        checks=checks,
        consent=payload.result.consent,
        prompt_text=described.prompt,
    )
