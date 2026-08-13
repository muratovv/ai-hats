"""Integrator compose seam — composes ONCE, returns a CompositionPayload (HATS-865).

The single place launch paths derive a composition for prompt delivery
(ADR-0005 D1): effective-role resolution, role-existence validation, the HITL
first-run ``set_role`` side effect, the audit snapshot (walking assembler
internals is legal HERE, never in bricks), and provider resolution. Bricks
receive the ready payload; they never import the composition layer.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from .composition_payload import CompositionPayload

if TYPE_CHECKING:
    from ai_hats_core import CompositionResult
    from .models import OverlayConfig
    from .role_spec import RoleSpec

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


def _runtime_overlay(resolver, spec: RoleSpec) -> OverlayConfig | None:
    """Build an ephemeral OverlayConfig from a RoleSpec, mapping names to component kinds."""
    import difflib

    if not spec.adds and not spec.removes:
        return None

    from .models import ComponentType, OverlayConfig
    from .role_spec import RoleSpecError

    traits = set(resolver.list_components(ComponentType.TRAIT))
    skills = set(resolver.list_components(ComponentType.SKILL))
    rules = set(resolver.list_components(ComponentType.RULE))
    roles = set(resolver.list_components(ComponentType.ROLE))

    adds_by_kind: dict[str, list[str]] = {"trait": [], "skill": [], "rule": []}
    removes_by_kind: dict[str, list[str]] = {"trait": [], "skill": [], "rule": []}

    all_kinds = [
        ("trait", traits),
        ("skill", skills),
        ("rule", rules),
    ]

    for op_names, target_dict in [(spec.adds, adds_by_kind), (spec.removes, removes_by_kind)]:
        for name in op_names:
            matches = [kind for kind, comp_set in all_kinds if name in comp_set]
            if len(matches) == 1:
                target_dict[matches[0]].append(name)
            elif len(matches) > 1:
                kinds_str = " and a ".join(matches)
                raise RoleSpecError(f"{name!r} is ambiguous — it is both a {kinds_str}")
            else:
                if name in roles:
                    raise RoleSpecError(
                        f"{name!r} is a role — only traits, rules and skills can be mixed in at runtime"
                    )

                all_known = sorted(traits | skills | rules)
                suggestions = difflib.get_close_matches(name, all_known, n=3)
                sugg_str = (
                    f". Did you mean: {', '.join(repr(s) for s in suggestions)}?"
                    if suggestions
                    else ""
                )
                raise RoleSpecError(f"{name!r} is not a known trait, rule or skill{sugg_str}")

    return OverlayConfig(
        add_traits=adds_by_kind["trait"],
        add_skills=adds_by_kind["skill"],
        add_rules=adds_by_kind["rule"],
        remove_traits=removes_by_kind["trait"],
        remove_skills=removes_by_kind["skill"],
        remove_rules=removes_by_kind["rule"],
    )


def _project_context(project_dir: Path, role_override: str | None, *, prefer_cwd: bool = False):
    """Assembler + cfg + THE role-fallback chain (override → active → default) + runtime overlay.

    The single home for the chain — build / preview / carry all resolve
    through here instead of growing copies (review 2026-07-04).

    ``prefer_cwd`` belongs to read-only callers alone (HATS-1501): letting a
    worktree's own library win is correct only when nothing is written.
    """
    from .assembler import Assembler
    from .library_paths import build_library_paths
    from .role_spec import parse_role_spec

    library_paths = build_library_paths(project_dir, prefer_cwd=True) if prefer_cwd else None
    asm = Assembler(project_dir, library_paths=library_paths)
    cfg = asm.project_config
    spec = parse_role_spec(role_override) if role_override else None
    effective_role = (spec.role if spec else None) or cfg.active_role or cfg.default_role
    runtime_overlay = _runtime_overlay(asm.resolver, spec) if spec else None
    return asm, cfg, effective_role, runtime_overlay, spec


class MissingProviderError(RuntimeError):
    """Raised by the compose seam when no provider is configured at all.

    The sibling of ``UnknownProviderError`` (a *named* provider absent from the
    registry) for the *unnamed* case; carries ``available`` so the CLI handler
    renders without re-querying the registry. Subclasses ``RuntimeError`` so
    ``config show-prompt``'s broad catch keeps working — the same
    backwards-compat move as ``UnknownProviderError(ValueError)`` (HATS-1224).
    """

    def __init__(self, available: list[str]) -> None:
        self.available = available
        super().__init__(
            "no provider configured in ai-hats.yaml. Run: ai-hats config set -p <provider>"
        )


def _effective_provider(cfg, override: str | None) -> str:
    """The provider-fallback chain: override → cfg.provider, loud when absent."""
    eff = override or cfg.provider
    if not eff:
        from .providers import provider_names

        raise MissingProviderError(provider_names())
    return eff


def _compose_validated(
    asm,
    effective_role,
    *,
    runtime_overlay: OverlayConfig | None = None,
    explicit_role: str | None,
    spec: RoleSpec | None = None,
    label: str,
):
    """Compose via the facade; an explicitly requested role validates existence
    before (``RoleNotFoundError``) and errors after (``RuntimeError``) — the
    former ``compose_role`` step contract."""
    from .materialize import compose_for_role

    if explicit_role:
        from .models import ComponentType

        base_role = spec.role if spec else explicit_role
        available = asm.resolver.list_components(ComponentType.ROLE)
        if base_role not in available:
            raise RoleNotFoundError(base_role, available)

    if runtime_overlay is not None:
        result = compose_for_role(asm, effective_role, runtime_overlay=runtime_overlay)
    else:
        result = compose_for_role(asm, effective_role)

    if explicit_role and result.errors:
        raise RuntimeError(f"{label}: failed to resolve role {explicit_role!r}: {result.errors}")
    return result


def _maybe_sync_active_role(
    asm,
    cfg,
    effective_role,
    eff_provider,
    *,
    interactive,
    role_override,
    warnings_sink=None,
    result=None,
):
    """HITL first-run / provider-switch: persist ``active_role`` before the
    session starts (hoisted from ``WrapRunner.run``, semantics intact).

    ``warnings_sink`` collects the set_role materialize warnings so the caller can
    route them through the read-hold instead of a bare pre-launch print (HATS-970).
    ``result`` is the seam's composition of ``effective_role`` (HATS-1435)."""
    first_run_hitl = interactive and effective_role and not role_override
    if first_run_hitl and (not cfg.active_role or cfg.provider != eff_provider):
        asm.set_role(effective_role, eff_provider, warnings_sink=warnings_sink, result=result)
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
    validation, the provider check (``MissingProviderError``), provider
    resolution, then the HITL first-run ``set_role`` side effect.
    ``strict=False`` skips the explicit-role raises for tolerant callers
    (retro reviewer spawn — HATS-271 owns its failure mode). ``interactive``
    gates ONLY that ``set_role`` persist — ``provider_name`` wins over
    ``cfg.provider`` on either path (HATS-1218).
    """
    from ai_hats_observe import AuditWriter, Session
    from .providers import get_provider

    asm, cfg, effective_role, runtime_overlay, spec = _project_context(project_dir, role_override)
    result = _compose_validated(
        asm,
        effective_role,
        runtime_overlay=runtime_overlay,
        explicit_role=role_override if strict else None,
        spec=spec,
        label="compose_role",
    )

    # HATS-1218: the batch arm used to hard-read cfg and drop the override here.
    eff_provider = _effective_provider(cfg, provider_name)
    provider = get_provider(eff_provider)

    startup_warnings: list[str] = []
    cfg = _maybe_sync_active_role(
        asm,
        cfg,
        effective_role,
        eff_provider,
        interactive=interactive,
        role_override=role_override,
        warnings_sink=startup_warnings,
        result=result,
    )

    from .role_spec import format_role_spec

    return CompositionPayload(
        result=result,
        provider=provider,
        effective_role=effective_role,
        role_expression=format_role_spec(
            effective_role,
            spec.adds if spec else (),
            spec.removes if spec else (),
        ),
        snapshot=_composition_snapshot(
            asm, effective_role, result, runtime_overlay=runtime_overlay, spec=spec
        ),
        hooks=asm.hooks,
        static_cost_analyzer=_static_cost_analyzer(project_dir),
        channel=cfg.harness.channel.value,
        startup_warnings=tuple(startup_warnings),
        # HATS-867: observe factories threaded runner→finalize pipelines.
        # HATS-948: the audit writer carries the provider's transcript parser.
        session_factory=Session,
        audit_writer_factory=partial(AuditWriter, parser=provider.transcript_parser()),
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
    agent see". Raises ``RuntimeError`` (the no-role case) or its
    ``MissingProviderError`` subclass, both of which ``config show-prompt``
    renders as a friendly exit 2.
    """
    from .materialize import compose_for_role
    from .providers import get_provider

    asm, cfg, eff_role, runtime_overlay, _spec = _project_context(
        project_dir, role, prefer_cwd=True
    )
    if not eff_role:
        raise RuntimeError(
            "materialize_system_prompt: no role to materialize "
            "(no --role override, no active_role/default_role in "
            "ai-hats.yaml). Set one or pass `role=...` to the step."
        )
    eff_provider = _effective_provider(cfg, provider)
    if runtime_overlay is not None:
        result = compose_for_role(asm, eff_role, runtime_overlay=runtime_overlay)
    else:
        result = compose_for_role(asm, eff_role)
    if result.errors:
        raise RuntimeError(
            f"materialize_system_prompt: compose errors for role {eff_role!r}: {result.errors}"
        )
    return CompositionPayload(
        result=result,
        provider=get_provider(eff_provider),
        effective_role=eff_role,
    )


def compose_for_checks(project_dir: Path, role: str | None = None) -> CompositionResult | None:
    """Fail-CLOSED compose for the ``checks:`` gate channel (HATS-1141).

    ``role`` is the session's own role expression when a session is asking, and
    ``None`` only outside one — where ``active_role`` genuinely is the answer.
    It was hardcoded ``None`` through HATS-1594, so a session launched with
    ``--role`` had its gates composed from whatever the config still said.

    The exact opposite of :func:`compose_for_carry` below, and deliberately so:
    carry degrades to ``None`` because trouble there must never block a worktree,
    while a gate that cannot compose must refuse — silence is HYP-078 (ADR-0019
    D9 / R6). ``result.errors`` is left to the caller.

    ``None`` for a project with **no active role** is not an exception to that.
    Bindings are collected per-role over a ``CompositionResult`` (ADR-0019 D7),
    so with no role there is no composition and therefore no binding — refusing
    would be refusing on the absence of the very thing that would carry a gate.
    A role that IS set but does not resolve still refuses, via ``result.errors``.
    """  # comment-length: allow — the contrast with its neighbour IS the contract
    from .materialize import compose_for_role

    asm, _cfg, effective, runtime_overlay, _spec = _project_context(project_dir, role)
    if not effective:
        return None
    return compose_for_role(asm, effective, runtime_overlay=runtime_overlay)


# HATS-1594 retired `session_skills_root_for_checks`. It re-read
# `ProjectConfig.provider` to find the mirror, so a session launched with `-p`
# resolved against a surface it was not running. The root is now decided once at
# launch and carried on `SessionIdentity.skills_root` — there is no second
# lookup left to disagree.


def compose_for_carry(project_dir: Path, role: str | None = None):
    """Fail-open compose for worktree-carry collection; a ``CompositionResult``
    or ``None``. Tracker-side callers route here — TEMP until HATS-866 re-cuts
    tracker→wt via the ``needs_worktree`` effect. An *exception* degrades to
    ``None`` with a WARN: carry trouble must never block worktree creation.

    ``result.errors`` is left to the caller, exactly as in ``compose_for_checks``
    above — ``wt_carry.collect_carry_for_role`` warns there and keeps whatever
    composed, because only it knows how many carry rows survived (HATS-1592).
    """
    try:
        asm, _cfg, effective, runtime_overlay, _spec = _project_context(project_dir, role)
        if not effective:
            return None
        from .materialize import compose_for_role

        return compose_for_role(asm, effective, runtime_overlay=runtime_overlay)
    except Exception as exc:  # noqa: BLE001 — never block create on carry collection
        logger.warning(
            "worktree carry: could not compose role %r: %s — dropping carry",
            role,
            exc,
        )
        return None


def _composition_snapshot(
    assembler,
    role_name: str,
    result: CompositionResult,
    *,
    runtime_overlay: OverlayConfig | None = None,
    spec: RoleSpec | None = None,
) -> dict:
    """Build the composition snapshot dict for ``Session.init_audit`` (HATS-442).

    Moved from ``runtime_common`` (HATS-865): it walks private Assembler API
    (overlays + provenance), so it computes at the compose seam and the DICT
    travels down in the payload — bricks never drive assembler machinery.
    """
    try:
        effective_traits = assembler._effective_traits(role_name, runtime_overlay=runtime_overlay)
        provenance = assembler._get_overlay_provenance(
            role_name, result=result, runtime_overlay=runtime_overlay
        )
    except Exception as exc:
        # Defensive: a broken overlay shouldn't kill session start.
        logger.warning(
            "composition snapshot failed for role %r: %s — audit.md will "
            "lack the composition section",
            role_name,
            exc,
        )
        return {}
    snap = {
        "traits": effective_traits,
        "rules": [r.name for r in result.rules],
        "skills": [s.name for s in result.skills],
        "provenance": provenance,
    }
    if spec and (spec.adds or spec.removes):
        snap["runtime"] = {
            "spec": spec.raw,
            "add": list(spec.adds),
            "remove": list(spec.removes),
        }
    return snap


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


def resolve_provider_for_help(provider_name: str | None, role_name: str | None):
    """Best-effort provider resolution for CLI help (e.g., ai-hats --help)."""
    from .providers import get_provider
    from .cli._helpers import _project_dir

    if provider_name:
        try:
            return get_provider(provider_name)
        except Exception:  # silent-ok: best-effort provider resolution for --help
            return None

    if role_name:
        try:
            asm, cfg, effective_role, _runtime, _spec = _project_context(_project_dir(), role_name)
            eff = cfg.provider
            if eff:
                return get_provider(eff)
        except Exception:  # silent-ok: best-effort provider resolution for --help  # noqa: S110
            pass

    return None
