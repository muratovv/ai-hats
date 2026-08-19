"""``--dry-run``: what a launch would deliver, without delivering it (HATS-1211).

Runs the real assembly with a ``PlanMaterializer``, so the report is the session
minus the spawn — not a reconstruction of it. Nothing is written: no session
record, no worktree, no ownership hold, no audit.
"""

from __future__ import annotations

from pathlib import Path

from .check_snapshot import describe_checks
from .materialization import PlanMaterializer
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

__all__ = ["AT_LAUNCH", "DRY_RUN_SESSION_ID", "dry_run_automate", "dry_run_hitl"]


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


def dry_run_hitl(
    project_dir: Path,
    *,
    role: str | None = None,
    provider: str | None = None,
    extra_args: list[str] | None = None,
    policy: SessionPolicy | None = None,
) -> SessionReport:
    """Build the HITL session in plan mode and report it."""
    from .composition_seam import build_preview_payload
    from .paths import session_cache_dir

    # The seam's read-only payload: same compose facade, no ``set_role`` write.
    payload = build_preview_payload(project_dir, role=role, provider=provider)
    prov = payload.provider
    eff_policy = policy or SessionPolicy()

    cache_dir = session_cache_dir(project_dir, DRY_RUN_SESSION_ID)
    before = _files_under(cache_dir)
    artifacts = BuiltArtifacts(port=PlanMaterializer())
    with prov.execution_context(project_dir):
        prov.build_session_artifacts(
            project_dir,
            payload.result,
            DRY_RUN_SESSION_ID,
            run_mode=RunMode.HITL,
            policy=eff_policy,
            artifacts=artifacts,
        )

    env = assemble_launch_env(
        prov,
        project_dir,
        cache_dir,
        session_id=DRY_RUN_SESSION_ID,
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
    checks, check_notes = describe_checks(
        prov, project_dir, payload.result, DRY_RUN_SESSION_ID, artifacts.port.plan
    )
    notes = [*check_notes, *_launch_notices(prov, project_dir, payload.result, eff_policy)]
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
        escapes=_detect_escapes(cache_dir, before),
        checks=checks,
        consent=payload.result.consent,
        notes=tuple(notes),
        prompt_text=artifacts.full_content,
    )


def _launch_notices(prov, project_dir: Path, result, policy: SessionPolicy) -> list[str]:
    """What the runner would say at startup about THIS surface (HATS-1548).

    A dry-run that stays quiet where the launch warns is the same silence the
    notices exist to remove — the operator learns it one session too late.
    """
    from .check_snapshot import legacy_launch_notices, surface_skew_notice

    if not prov.handles_artifact_categories():
        return legacy_launch_notices(prov.name, result, policy)
    skew = surface_skew_notice(prov.name, prov, project_dir, result)
    return [skew] if skew else []


def dry_run_automate(
    project_dir: Path,
    *,
    role: str | None = None,
    provider: str | None = None,
    task: str = "",
    ticket_id: str = "",
    model: str = "",
    policy: SessionPolicy | None = None,
) -> SessionReport:
    """Build the sub-agent session in plan mode and report it.

    Follows the runner's own AUTOMATE assembly rather than a tidier one, so the
    report shows what a sub-agent really gets — including, today, the paths that
    go around the port (see ``escapes``).
    """
    from .composition_seam import build_preview_payload
    from .paths import session_cache_dir

    # The seam's read-only payload: same compose facade, no ``set_role`` write.
    payload = build_preview_payload(project_dir, role=role, provider=provider)
    prov = payload.provider
    eff_policy = policy or SessionPolicy()

    cache_dir = session_cache_dir(project_dir, DRY_RUN_SESSION_ID)
    before = _files_under(cache_dir)
    artifacts = BuiltArtifacts(port=PlanMaterializer())
    with prov.execution_context(project_dir):
        prov.build_session_artifacts(
            project_dir,
            payload.result,
            DRY_RUN_SESSION_ID,
            run_mode=RunMode.AUTOMATE,
            policy=eff_policy,
            artifacts=artifacts,
        )

    checks, notes = describe_checks(
        prov, project_dir, payload.result, DRY_RUN_SESSION_ID, artifacts.port.plan
    )
    env = assemble_launch_env(
        prov,
        project_dir,
        cache_dir,
        session_id=DRY_RUN_SESSION_ID,
        trace_path=AT_LAUNCH,
        role=payload.role_expression,
        root_pid=AT_LAUNCH,
        extra_env=artifacts.extra_env,
        run_mode=RunMode.AUTOMATE,
        claim=False,
    )
    described = prov.describe_automate_launch(
        project_dir,
        payload.result,
        DRY_RUN_SESSION_ID,
        artifacts,
        task=task,
        ticket_id=ticket_id,
        model=model,
        env=env,
    )

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
        escapes=_detect_escapes(cache_dir, before),
        notes=notes,
        checks=checks,
        consent=payload.result.consent,
        prompt_text=described.prompt,
    )
