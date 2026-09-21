"""Surfaces — the area's only entrance (ADR-0026 D5/D14).

A surface is one way of running an agent. Every implementation answers the same
contract and none of them is named here: a caller that needs *a* surface asks
``ai_hats.surface_registry.get_surface``, and a caller that needs *the* contract —
to implement it, or to type a parameter — imports it from this facade.

Importing a name from under this package instead of from here is what
``tests/test_area_boundary.py`` refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_hats_core.lazy import lazy_facade

if TYPE_CHECKING:  # the names below resolve for a reader and a type checker
    from .contract import (
        MetricsSink,  # noqa: F401
        SubagentEngine,  # noqa: F401
        Surface,  # noqa: F401
        SurfaceHint,  # noqa: F401
        SurfaceRunResult,  # noqa: F401
        TranscriptResolver,  # noqa: F401
    )
    from .hook_channel import (
        ChainDecision,  # noqa: F401
        ChainVerdict,  # noqa: F401
        HookCall,  # noqa: F401
        HookEvent,  # noqa: F401
        HookRow,  # noqa: F401
        run_chain,  # noqa: F401
    )
    from .managed_tags import sweep_stale_managed_tags  # noqa: F401
    from .mcp import StdioMCPServer  # noqa: F401
    from .plan import (
        Applied,  # noqa: F401
        CompositionPlan,  # noqa: F401
        ContextUnwritten,  # noqa: F401
        Digested,  # noqa: F401
        ExternalHook,  # noqa: F401
        Host,  # noqa: F401
        Launch,  # noqa: F401
        Launched,  # noqa: F401
        LaunchFlags,  # noqa: F401
        MaterializationPlan,  # noqa: F401
        Outcome,  # noqa: F401
        PlanRefused,  # noqa: F401
        Prompt,  # noqa: F401
        PromptBlock,  # noqa: F401
        PromptMember,  # noqa: F401
        TraceEntry,  # noqa: F401
        apply,  # noqa: F401
        checks_record,  # noqa: F401
        composition_record,  # noqa: F401
        context_entry,  # noqa: F401
        context_text,  # noqa: F401
        trace_record,  # noqa: F401
        validate,  # noqa: F401
    )

    # ``adapt`` is bound lazily alone: the adapter reaches the composition layer,
    # which reaches this facade — a static import here would close that cycle.
    from .profiles import hook_profile  # noqa: F401

# comment-length: allow — an alias has to say what it does NOT cover
# Deprecated aliases, so an out-of-tree surface written against
# `Provider` keeps importing (the `LaunchProvider` precedent). Names only — a
# subclass overriding `provider_hints` is no longer called, which is a real
# break the CHANGELOG names.
_ALIASES = {
    "Provider": "Surface",
    "ProviderHint": "SurfaceHint",
    "ProviderRunResult": "SurfaceRunResult",
}

# Bound lazily (PEP 562): the hook dispatchers live under this package and are a
# fresh process per tool call, so entering it must not cost `contract`.
_HOMES = {
    "ChainDecision": ".hook_channel",
    "ChainVerdict": ".hook_channel",
    "HookCall": ".hook_channel",
    "HookEvent": ".hook_channel",
    "HookRow": ".hook_channel",
    "StdioMCPServer": ".mcp",
    "hook_profile": ".profiles",
    "run_chain": ".hook_channel",
    "MetricsSink": ".contract",
    "Provider": ".contract",
    "ProviderHint": ".contract",
    "ProviderRunResult": ".contract",
    "SubagentEngine": ".contract",
    "Surface": ".contract",
    "SurfaceHint": ".contract",
    "SurfaceRunResult": ".contract",
    "TranscriptResolver": ".contract",
    "Applied": ".plan",
    "CompositionPlan": ".plan",
    "ContextUnwritten": ".plan",
    "Digested": ".plan",
    "ExternalHook": ".plan",
    "Host": ".plan",
    "Launch": ".plan",
    "LaunchFlags": ".plan",
    "Launched": ".plan",
    "MaterializationPlan": ".plan",
    "Outcome": ".plan",
    "PlanRefused": ".plan",
    "Prompt": ".plan",
    "PromptBlock": ".plan",
    "PromptMember": ".plan",
    "TraceEntry": ".plan",
    "adapt": ".plan_adapter",
    "apply": ".plan",
    "checks_record": ".plan",
    "composition_record": ".plan",
    "context_entry": ".plan",
    "context_text": ".plan",
    "sweep_stale_managed_tags": ".managed_tags",
    "trace_record": ".plan",
    "validate": ".plan",
}


__all__ = sorted(_HOMES)
__getattr__, __dir__ = lazy_facade(globals(), _HOMES, aliases=_ALIASES)
