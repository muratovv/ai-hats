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
    BuiltArtifacts,
    RunMode,
    SessionPolicy,
    assemble_launch_command,
)
from .session_report import SessionReport

# A real sid is minted by the session manager, which a dry-run must not touch.
# Fixed so reported paths are stable and diffable.
DRY_RUN_SESSION_ID = "dry-run"


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

    cache_dir = session_cache_dir(project_dir, DRY_RUN_SESSION_ID)
    before = _files_under(cache_dir)
    artifacts = BuiltArtifacts(port=PlanMaterializer())
    with prov.execution_context(project_dir):
        prov.build_session_artifacts(
            project_dir,
            payload.result,
            DRY_RUN_SESSION_ID,
            run_mode=RunMode.HITL,
            policy=policy or SessionPolicy(),
            artifacts=artifacts,
        )

    env = {
        **prov.get_env(cache_dir, project_dir),
        **artifacts.extra_env,
    }
    launch = assemble_launch_command(
        prov,
        extra_args=extra_args,
        session_args=artifacts.cli_args,
        provider_session_id="<assigned at launch>",
    )
    prompt = next((p for p in artifacts.materialized if p.suffix in (".md", ".MD")), None)
    checks, check_notes = describe_checks(
        prov, project_dir, payload.result, DRY_RUN_SESSION_ID, artifacts.port.plan
    )
    return SessionReport(
        role=payload.effective_role,
        provider=prov.name,
        run_mode=RunMode.HITL.value,
        policy=policy or SessionPolicy(),
        launch=launch,
        env=env,
        prompt=prompt,
        plan=artifacts.port.plan,
        cwd=str(project_dir),
        escapes=_detect_escapes(cache_dir, before),
        checks=checks,
        notes=check_notes,
    )


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
    from .constants import PROVIDER_CLAUDE
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
    if prov.name == PROVIDER_CLAUDE:
        launch = [f"{k}={v}" for k, v in sorted(artifacts.sdk_options.items())]
    else:
        role_ctx = artifacts.full_content or ""
        meta_prompt = _meta_prompt(role_ctx, task, ticket_id)
        flags = prov.model_flags(model) if model else []
        cmd = prov.get_cli_command() + artifacts.cli_args + flags
        launch = prov.get_run_command(cmd, meta_prompt)

    return SessionReport(
        role=payload.effective_role,
        provider=prov.name,
        run_mode=RunMode.AUTOMATE.value,
        policy=eff_policy,
        launch=launch,
        env=dict(artifacts.extra_env),
        prompt=next((p for p in artifacts.materialized if p.suffix == ".md"), None),
        plan=artifacts.port.plan,
        cwd="<worktree, assigned at launch>",
        escapes=_detect_escapes(cache_dir, before),
        notes=notes,
        checks=checks,
    )


def _meta_prompt(role_context: str, task: str, ticket_id: str) -> str:
    sections = []
    if role_context:
        sections.append(role_context)
    if task:
        sections.append(f"# TASK\n{task}")
    return "\n\n".join(sections)
