"""Integrator compose seam — composes ONCE, returns a CompositionPayload (HATS-865).

The single place launch paths derive a composition for prompt delivery
(ADR-0005 П1): effective-role resolution, role-existence validation, the HITL
first-run ``set_role`` side effect, the audit snapshot (walking assembler
internals is legal HERE, never in bricks), and provider resolution. Bricks
receive the ready payload; they never import the composition layer.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from .composition_payload import CompositionPayload

logger = logging.getLogger(__name__)


def make_session_manager(project_dir: Path):
    """A run-path ``SessionManager`` with the real ``EnvironmentRecovery`` wired.

    observe defaults to a package-pure no-op recovery (HATS-948); the integrator
    injects the version-GC recovery at this seam so it fires at the
    ``create_session`` chokepoint on every run (HATS-649). Read-only ``session``
    CLI paths never create sessions, so they keep the bare no-op default.
    """
    from .environment_recovery import EnvironmentRecovery
    from ai_hats_observe import SessionManager
    from .paths import runs_dir

    return SessionManager(
        project_dir,
        runs_dir=runs_dir(project_dir),
        recovery=EnvironmentRecovery(project_dir),
    )


class RoleNotFoundError(Exception):
    """Raised by the compose seam when an explicitly requested role is unknown.

    Carries the requested name plus the sorted list of available role names so
    the CLI handler can render a friendly error without re-querying the
    resolver (moved from ``pipeline.steps.compose`` in HATS-865 — validation
    now happens at the seam, before any pipeline runs).
    """

    def __init__(self, role: str, available: list[str]) -> None:
        self.role = role
        self.available = available
        super().__init__(f"Role {role!r} not found")


def _project_context(project_dir: Path, role_override: str | None):
    """Assembler + cfg + THE role-fallback chain (override → active → default).

    The single home for the chain — build / preview / carry all resolve
    through here instead of growing copies (review 2026-07-04).
    """
    from .assembler import Assembler

    asm = Assembler(project_dir)
    cfg = asm.project_config
    return asm, cfg, (role_override or cfg.active_role or cfg.default_role)


def _effective_provider(cfg, override: str | None, *, missing_hint: str) -> str:
    """The provider-fallback chain: override → cfg.provider, loud when absent."""
    eff = override or cfg.provider
    if not eff:
        raise RuntimeError(missing_hint)
    return eff


def _compose_validated(asm, effective_role, *, explicit_role: str | None, label: str):
    """Compose via the facade; an explicitly requested role validates existence
    before (``RoleNotFoundError``) and errors after (``RuntimeError``) — the
    former ``compose_role`` step contract."""
    from .materialize import compose_for_role

    if explicit_role:
        from .models import ComponentType

        available = asm.resolver.list_components(ComponentType.ROLE)
        if explicit_role not in available:
            raise RoleNotFoundError(explicit_role, available)
    result = compose_for_role(asm, effective_role)
    if explicit_role and result.errors:
        raise RuntimeError(
            f"{label}: failed to resolve role {explicit_role!r}: {result.errors}"
        )
    return result


def _maybe_sync_active_role(
    asm, cfg, effective_role, eff_provider, *, interactive, role_override, warnings_sink=None
):
    """HITL first-run / provider-switch: persist ``active_role`` before the
    session starts (hoisted from ``WrapRunner.run``, semantics intact).

    ``warnings_sink`` collects the set_role materialize warnings so the caller can
    route them through the read-hold instead of a bare pre-launch print (HATS-970)."""
    first_run_hitl = interactive and effective_role and not role_override
    if first_run_hitl and (not cfg.active_role or cfg.provider != eff_provider):
        asm.set_role(effective_role, eff_provider, warnings_sink=warnings_sink)
        return asm.project_config
    return cfg


def build_composition_payload(
    project_dir: Path,
    *,
    role_override: str | None = None,
    provider_name: str | None = None,
    interactive: bool = False,
    strict: bool = True,
) -> CompositionPayload:
    """Compose the effective role once and bundle everything runners need.

    Ordering preserves the pre-HATS-865 observable sequence: explicit-role
    validation, the interactive provider check (former ``launch_provider``
    message), provider resolution, then the HITL first-run ``set_role`` side
    effect. ``strict=False`` skips the explicit-role raises for tolerant
    callers (retro reviewer spawn — HATS-271 owns its failure mode).
    """
    from ai_hats_observe import AuditWriter, Session
    from .providers import get_provider

    asm, cfg, effective_role = _project_context(project_dir, role_override)
    result = _compose_validated(
        asm,
        effective_role,
        explicit_role=role_override if strict else None,
        label="compose_role",
    )

    if interactive:
        eff_provider = _effective_provider(
            cfg,
            provider_name,
            missing_hint="launch_provider: no provider configured. "
            "Run: ai-hats config set -p <provider>",
        )
    else:
        # Batch path never honoured a provider flag (SubAgentRunner read cfg).
        eff_provider = cfg.provider
    provider = get_provider(eff_provider)

    startup_warnings: list[str] = []
    cfg = _maybe_sync_active_role(
        asm, cfg, effective_role, eff_provider,
        interactive=interactive, role_override=role_override,
        warnings_sink=startup_warnings,
    )

    return CompositionPayload(
        result=result,
        provider=provider,
        effective_role=effective_role,
        snapshot=_composition_snapshot(asm, effective_role, result),
        hooks=asm.hooks,
        static_cost_analyzer=_static_cost_analyzer(project_dir),
        channel=cfg.harness.channel.value,
        startup_warnings=tuple(startup_warnings),
        # HATS-867: observe factories threaded runner→finalize pipelines.
        # HATS-948: the audit writer carries the provider's transcript parser.
        session_factory=Session,
        audit_writer_factory=partial(
            AuditWriter, parser=provider.transcript_parser()
        ),
        # HATS-1087: the provider knows WHERE its transcript lives; the parser
        # (above) knows HOW to read it. Both ride the payload to the finalize steps.
        transcript_resolver=provider.resolve_transcript,
    )


def build_preview_payload(
    project_dir: Path,
    *,
    role: str | None = None,
    provider: str | None = None,
) -> CompositionPayload:
    """Read-only payload for the ``materialize_system_prompt`` preview surface.

    No ``set_role`` side effect, no hooks/analyzer — pure "what would the
    agent see". Raises ``RuntimeError`` with the step's historical messages so
    ``config show-prompt`` UX is unchanged.
    """
    from .materialize import compose_for_role
    from .providers import get_provider

    asm, cfg, eff_role = _project_context(project_dir, role)
    if not eff_role:
        raise RuntimeError(
            "materialize_system_prompt: no role to materialize "
            "(no --role override, no active_role/default_role in "
            "ai-hats.yaml). Set one or pass `role=...` to the step."
        )
    eff_provider = _effective_provider(
        cfg,
        provider,
        missing_hint="materialize_system_prompt: no provider configured. "
        "Set `provider:` in ai-hats.yaml or pass `provider=...`.",
    )
    result = compose_for_role(asm, eff_role)
    if result.errors:
        raise RuntimeError(
            f"materialize_system_prompt: compose errors for role "
            f"{eff_role!r}: {result.errors}"
        )
    return CompositionPayload(
        result=result,
        provider=get_provider(eff_provider),
        effective_role=eff_role,
    )


def compose_for_carry(project_dir: Path, role: str | None = None):
    """Fail-open compose for worktree-carry collection; ``(result, hooks)`` or
    ``None``. Tracker-side callers route here — TEMP until HATS-866 re-cuts
    tracker→wt via the ``needs_worktree`` effect. Any failure degrades to
    ``None`` with a WARN: carry trouble must never block worktree creation.
    """
    try:
        asm, _cfg, effective = _project_context(project_dir, role)
        if not effective:
            return None
        from .materialize import compose_for_role

        return compose_for_role(asm, effective), asm.hooks
    except Exception as exc:  # noqa: BLE001 — never block create on carry collection
        logger.warning(
            "worktree carry: could not compose role %r: %s — dropping carry",
            role,
            exc,
        )
        return None


def _composition_snapshot(assembler, role_name: str, result) -> dict:
    """Build the composition snapshot dict for ``Session.init_audit`` (HATS-442).

    Moved from ``runtime_common`` (HATS-865): it walks private Assembler API
    (overlays + provenance), so it computes at the compose seam and the DICT
    travels down in the payload — bricks never drive assembler machinery.
    """
    try:
        base_cfg = assembler.resolver.resolve_role_config(role_name)
        effective_traits: list[str] = list(base_cfg.composition.traits) if base_cfg else []
        for layer in (
            assembler._get_global_overlay(role_name),
            assembler._get_overlay(role_name),
        ):
            if layer is None:
                continue
            for name in layer.remove_traits:
                if name in effective_traits:
                    effective_traits.remove(name)
            for name in layer.add_traits:
                if name not in effective_traits:
                    effective_traits.append(name)
        provenance = assembler._get_overlay_provenance(role_name)
    except Exception as exc:
        # Defensive: a broken overlay shouldn't kill session start.
        logger.warning(
            "composition snapshot failed for role %r: %s — audit.md will "
            "lack the composition section",
            role_name,
            exc,
        )
        return {}
    return {
        "traits": effective_traits,
        "rules": [r.name for r in result.rules],
        "skills": [s.name for s in result.skills],
        "provenance": provenance,
    }


def _static_cost_analyzer(project_dir: Path):
    """Carve-out #1 (HATS-865): finalize learns the role only at run time (from
    transcripts), so the static always-on cross-check stays a late-bound
    callable — composed here, threaded runner → finalize initial state."""

    def analyze(role: str) -> dict | None:
        from .assembler import Assembler
        from .composer import Composer
        from .costs import analyze_composition

        composer = Composer(Assembler(project_dir).resolver)
        breakdown = analyze_composition(composer, role, exact=False)
        return {
            "role": role,
            "total_tokens": breakdown.total_tokens,
            # HATS-957: skill bodies load on demand, not always-on. Split so the
            # reported "always-on" figure excludes them (they show separately).
            "always_on_tokens": breakdown.always_on_tokens,
            "on_demand_tokens": breakdown.on_demand_tokens,
            "exact": breakdown.exact,
            "components": [
                {
                    "name": c.name,
                    "category": c.category,
                    "tokens": c.tokens,
                    "always_on_tokens": c.always_on_tokens,
                    "on_demand_tokens": c.on_demand_tokens,
                }
                for c in breakdown.components
            ],
        }

    return analyze
