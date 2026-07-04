"""Integrator compose seam — composes ONCE, returns a CompositionPayload (HATS-865).

The single place launch paths derive a composition for prompt delivery
(ADR-0005 П1): effective-role resolution, role-existence validation, the HITL
first-run ``set_role`` side effect, the audit snapshot (walking assembler
internals is legal HERE, never in bricks), and provider resolution. Bricks
receive the ready payload; they never import the composition layer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .composition_payload import CompositionPayload

logger = logging.getLogger(__name__)


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
    validation (``RoleNotFoundError`` / compose-errors ``RuntimeError`` — the
    former ``compose_role`` step contract), the interactive provider check
    (former ``launch_provider`` message), provider resolution, then the HITL
    first-run / provider-switch ``set_role`` side effect (former
    ``WrapRunner.run``). ``strict=False`` skips the explicit-role raises for
    tolerant callers (retro reviewer spawn — HATS-271 owns its failure mode).
    """
    from .assembler import Assembler
    from .materialize import compose_for_role
    from .providers import get_provider

    asm = Assembler(project_dir)
    cfg = asm.project_config
    effective_role = role_override or cfg.active_role or cfg.default_role

    if strict and role_override:
        from .models import ComponentType

        available = asm.resolver.list_components(ComponentType.ROLE)
        if role_override not in available:
            raise RoleNotFoundError(role_override, available)

    result = compose_for_role(asm, effective_role)
    if strict and role_override and result.errors:
        raise RuntimeError(
            f"compose_role: failed to resolve role {role_override!r}: {result.errors}"
        )

    if interactive:
        eff_provider = provider_name or cfg.provider
        if not eff_provider:
            raise RuntimeError(
                "launch_provider: no provider configured. "
                "Run: ai-hats config set -p <provider>"
            )
    else:
        # Batch path never honoured a provider flag (SubAgentRunner read cfg).
        eff_provider = cfg.provider
    provider = get_provider(eff_provider)

    if interactive and effective_role and not role_override:
        # HITL first-run / provider-switch: sync active_role on disk before
        # the session starts (hoisted from WrapRunner.run, semantics intact).
        if not cfg.active_role or cfg.provider != eff_provider:
            asm.set_role(effective_role, eff_provider)
            cfg = asm.project_config

    return CompositionPayload(
        result=result,
        provider=provider,
        effective_role=effective_role,
        snapshot=_composition_snapshot(asm, effective_role, result),
        hooks=asm.hooks,
        static_cost_analyzer=_static_cost_analyzer(project_dir),
        channel=cfg.harness.channel.value,
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
    from .assembler import Assembler
    from .materialize import compose_for_role
    from .providers import get_provider

    asm = Assembler(project_dir)
    cfg = asm.project_config
    eff_role = role or cfg.active_role or cfg.default_role
    if not eff_role:
        raise RuntimeError(
            "materialize_system_prompt: no role to materialize "
            "(no --role override, no active_role/default_role in "
            "ai-hats.yaml). Set one or pass `role=...` to the step."
        )
    eff_provider = provider or cfg.provider
    if not eff_provider:
        raise RuntimeError(
            "materialize_system_prompt: no provider configured. "
            "Set `provider:` in ai-hats.yaml or pass `provider=...`."
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
    except Exception:
        # Defensive: a broken overlay shouldn't kill session start. Fall
        # back to "no snapshot" — audit.md just won't have the section.
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
            "exact": breakdown.exact,
            "components": [
                {"name": c.name, "category": c.category, "tokens": c.tokens}
                for c in breakdown.components
            ],
        }

    return analyze
