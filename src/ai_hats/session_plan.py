"""One session on the plan: probe the machine, plan, launch, record (ADR-0036 D2–D6).

Everything here takes the plan's composition half and knows nothing of the
composer's own value — the same path serves whichever processing produced it.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from .session_artifacts import AT_LAUNCH, RunMode, SessionPolicy
from .surfaces import (
    Applied,
    CompositionPlan,
    Host,
    Launched,
    LaunchFlags,
    MaterializationPlan,
    Surface,
    apply,
    checks_record,
    composition_record,
    context_text,
)


def plans(surface: Surface) -> bool:
    """Whether the surface has a planner — the one predicate the runners,
    ``--dry-run-experimental`` and ``show-prompt`` branch on while the old
    path still carries the surfaces that have none."""
    return type(surface).plan is not Surface.plan


def probe_host(
    environ: Mapping[str, str] | None = None,
    *,
    python: str = sys.executable,
    which: Callable[..., str | None] = shutil.which,
    surface: Surface | None = None,
) -> Host:
    """The one read of the machine planning is allowed, taken before it.

    Every command the consent gate can wrap is resolved here, whether or not
    the composition asks for it: the host is a fact of the machine, not of the
    role, and a planner that finds no key refuses on its own terms. The
    person's configuration home is the surface's to enumerate (``probe_home``),
    so it is taken here too, for the surface in hand.
    """
    from ai_hats_library.hooks.consent_gate import operations

    from .consent_wrapper import original_lookup_path

    env = os.environ if environ is None else environ
    path = original_lookup_path(env.get("PATH", ""))
    commands = {
        name: Path(found).resolve()  # a link to an inherited wrapper is caught by what it points at
        for name in operations.wrapped_surfaces(operations.REGISTRY)
        if (found := which(name, path=path))
    }
    home = surface.probe_home(env) if surface is not None else None
    return Host(python=Path(python), path=path, commands=commands, home=home)


def plan_session(
    composition: CompositionPlan,
    surface: Surface,
    *,
    run_mode: RunMode,
    policy: SessionPolicy,
    root: Path,
    layout: ProjectLayout,
    host: Host,
) -> MaterializationPlan:
    """The surface's plan, with the role's command middleware on a HITL launch."""
    from .consent_wrapper import plan_consent

    mode = RunMode(run_mode)
    plan = surface.plan(
        composition, run_mode=mode, policy=policy, root=root, layout=layout, host=host
    )
    if mode is RunMode.HITL:
        plan = plan_consent(plan, surface, layout, host)
    return plan


_RESUME_FLAGS = ("--resume", "--continue", "-c")


def launch(plan: MaterializationPlan, flags: LaunchFlags, *, layout: ProjectLayout) -> Launched:
    """The pair applied (ADR-0036 D4): argv or option document, and the environment."""
    from .surface_registry import get_surface

    surface = get_surface(plan.surface)
    env = launch_env(plan, surface, flags, layout=layout)
    if plan.run_mode is RunMode.AUTOMATE:
        return surface.automate_launch(plan, flags, env, layout=layout)
    cmd = surface.get_cli_command(list(flags.extra_args))
    cmd.extend(plan.launch.args or ())
    is_resume = any(f in flags.extra_args for f in _RESUME_FLAGS)
    argv = surface.get_cli_launch_args(cmd, flags.provider_session_id or "", is_resume)
    return Launched(args=tuple(argv), sdk_options=None, env=env, prompt=context_text(plan))


def launch_env(
    plan: MaterializationPlan, surface: Surface, flags: LaunchFlags, *, layout: ProjectLayout
) -> dict[str, str]:
    """Everything ai-hats adds to the child's environment: the plan's own,
    then the session's identity and the flags, which override anything
    upstream spelled differently."""
    from ai_hats_observe.session import session_env

    from .constants import ENV_ROOT_PID
    from .session_artifacts import withheld_from_child
    from .session_identity import SessionIdentity

    identity = SessionIdentity(
        id=flags.session_id,
        role=plan.composition.identity,
        provider=surface.name,
        project_dir=layout.root,
        session_dir=flags.session_dir,
        skills_root=str(surface.session_skills_root(layout, flags.session_id) or ""),
        session_cache_dir=str(plan.root),
    )
    withheld = withheld_from_child() if plan.run_mode is RunMode.AUTOMATE else {}
    return {
        **withheld,
        **session_env(flags.session_id, flags.trace_path),
        **plan.env,
        **(surface.claim_launch_env(flags.session_dir, layout) if flags.claim else {}),
        **identity.to_env(),
        ENV_ROOT_PID: flags.root_pid,
    }


def session_record(
    plan: MaterializationPlan,
    launched: Launched,
    applied: Applied | None = None,
    *,
    role: str,
    cwd: str = "",
    notes: Sequence[str] = (),
) -> dict:
    """The record ``role_materialization.json`` holds and the dry-run prints:
    the plan, the launch and what application did — bytes stay out."""
    from .consent_wrapper import CONSENT_APP
    from .session_report import consent_row
    from .surface_registry import get_surface

    surface = get_surface(plan.surface)
    materialized = []
    for i, entry in enumerate(plan.entries):
        row: dict[str, object] = {
            "kind": entry.kind.value,
            "target": str(entry.target),
            "source": str(entry.source) if entry.source else None,
            "size": entry.size,
            "digest": entry.digest,
        }
        if applied is not None:
            row["outcome"] = applied.entries[i].outcome.value
            row["files"] = applied.entries[i].files
        materialized.append(row)
    return {
        "role": role,
        "provider": plan.surface,
        "run_mode": plan.run_mode.value,
        "cwd": cwd,
        "policy": {
            "context": plan.policy.context,
            "hooks": plan.policy.hooks,
            "settings": plan.policy.settings,
        },
        "launch": surface.describe_launch(launched),
        "env_keys": sorted(launched.env),
        "prompt": str(plan.context) if plan.context is not None else None,
        "materialized": materialized,
        "checks": checks_record(plan),
        "consent": [
            consent_row(h) for h in plan.composition.hooks.external if h.app == CONSENT_APP
        ],
        "notes": list(notes),
        "composition": composition_record(plan.composition),
    }


def render_record(record: dict, *, full: bool = False, prompt_text: str | None = None) -> str:
    from .session_report import render_report

    return render_report(record, full=full, prompt_text=prompt_text)


@dataclass(frozen=True)
class Preview:
    """What ``--dry-run-experimental`` shows: the record, and the prompt bytes
    the agent would be handed (outside the record, as ``meta_prompt.txt`` is)."""

    record: dict
    prompt: str


def preview(
    layout: ProjectLayout,
    *,
    role: str | None,
    provider: str | None,
    run_mode: RunMode,
    extra_args: Sequence[str] = (),
    brief: str | None = None,
    model: str | None = None,
    policy: SessionPolicy | None = None,
    materialize: bool = False,
) -> Preview:
    """Plan for a stub root and launch with placeholder flags; write nothing
    unless ``materialize``, which applies the plan to the fixed dry-run root."""
    from .composition_seam import build_preview_payload
    from .dry_run import DRY_RUN_MATERIALIZE_SESSION_ID, DRY_RUN_SESSION_ID

    payload = build_preview_payload(layout.root, role=role, provider=provider)
    surface = payload.provider
    if not plans(surface):
        raise RuntimeError(f"surface {surface.name!r} does not plan a session yet; use --dry-run")
    if payload.plan is None:
        raise RuntimeError("the seam adapted no composition")
    mode = RunMode(run_mode)
    sid = DRY_RUN_MATERIALIZE_SESSION_ID if materialize else DRY_RUN_SESSION_ID
    root = layout.cache.session(sid)
    plan = plan_session(
        payload.plan,
        surface,
        run_mode=mode,
        policy=policy or SessionPolicy(),
        root=root,
        layout=layout,
        host=probe_host(),
    )
    flags = LaunchFlags(
        session_id=sid,
        session_dir=root,
        trace_path=AT_LAUNCH,
        root_pid=AT_LAUNCH,
        provider_session_id=AT_LAUNCH,
        extra_args=tuple(extra_args),
        model=model,
        brief=brief,
        claim=False,
    )
    launched = launch(plan, flags, layout=layout)
    applied = None
    notes = [d.render() for d in payload.diagnostics]
    if materialize:
        # A fixed sid means one directory for every run; the sync then starts
        # from nothing, and apply serialises the writes on its own lock.
        if root.exists():
            shutil.rmtree(root)  # safe-delete: ok synthetic dry-run sid
        applied = apply(plan)
        notes.append(f"materialized session tree written to disk at {root}")
        notes.append(
            f"session tree uses synthetic session_id '{sid}'; real sessions mint their own"
        )
    cwd = str(layout.root) if mode is RunMode.HITL else "<worktree, assigned at launch>"
    record = session_record(
        plan, launched, applied, role=payload.effective_role, cwd=cwd, notes=notes
    )
    return Preview(record=record, prompt=launched.prompt)


__all__ = [
    "Preview",
    "launch",
    "launch_env",
    "plan_session",
    "plans",
    "preview",
    "probe_host",
    "render_record",
    "session_record",
]
