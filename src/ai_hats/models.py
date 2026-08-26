"""Facade over the per-domain schemas (HATS-863) — pure re-exports.

The god-module was split per ADR-0014 §2: libraries/config own their schemas
(wt schema lives in ``ai_hats_wt.carry``; the tracker re-export was dropped in
HATS-1260). T18 dismantles what remains when those domains lift into packages.
"""

from __future__ import annotations

from .config import (  # noqa: F401
    KNOWN_SCHEMA_VERSION,
    Channel,
    FeedbackConfig,
    FeedbackPolicy,
    HarnessConfig,
    OverlayConfig,
    ProjectConfig,
    ProjectConfigError,
    SessionRetroConfig,
    SmartThreshold,
    UserConfig,
    UserConfigError,
    WorktreeConfig,
    _DEPRECATED_PROJECT_FIELDS,
)
from .provenance import ComponentLayer  # noqa: F401
from .libraries.models import (  # noqa: F401
    GIT_HOOK_EVENTS,
    RUNTIME_HOOK_EVENTS,
    AppBinding,
    CheckBindingError,
    ComponentConfig,
    ComponentKeyError,
    ComponentType,
    Composition,
    LeftoverSidecarHooksError,
    RuntimeHook,
    SkillMetadata,
    parse_app_bindings,
    resolve_namespace,
)
