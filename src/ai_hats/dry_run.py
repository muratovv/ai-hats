"""``--dry-run``: what a launch would deliver, without delivering it (HATS-1211).

Runs the real assembly with a ``PlanMaterializer``, so the report is the session
minus the spawn — not a reconstruction of it. Nothing is written: no session
record, no worktree, no ownership hold, no audit.
"""

from __future__ import annotations

from pathlib import Path

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


def dry_run_hitl(
    project_dir: Path,
    *,
    role: str | None = None,
    provider: str | None = None,
    extra_args: list[str] | None = None,
    policy: SessionPolicy | None = None,
) -> SessionReport:
    """Build the HITL session in plan mode and report it."""
    from .composition_seam import build_composition_payload
    from .paths import session_cache_dir

    # interactive=False on purpose: it gates the first-run ``set_role`` write
    # (composition_seam.py:96-108), and a preview must not configure the project.
    payload = build_composition_payload(
        project_dir, role_override=role, provider_name=provider, interactive=False
    )
    prov = payload.provider

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

    session_dir = session_cache_dir(project_dir, DRY_RUN_SESSION_ID)
    env = {
        **prov.get_env(session_dir, project_dir),
        **artifacts.extra_env,
    }
    launch = assemble_launch_command(
        prov,
        extra_args=extra_args,
        session_args=artifacts.cli_args,
        provider_session_id="<assigned at launch>",
    )
    prompt = next(
        (p for p in artifacts.materialized if p.suffix in (".md", ".MD")), None
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
    )
