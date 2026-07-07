"""Facade over the per-domain schemas (HATS-863) — pure re-exports.

The god-module was split per ADR-0014 §2: tracker/libraries/config own their
schemas (wt schema lives in ``ai_hats_wt.carry``). T16 lifted the tracker schema
into ``ai_hats_tracker`` (re-exported below); T18 dismantles what remains
(config + libraries) when those domains lift into workspace packages.
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
from .libraries.models import (  # noqa: F401
    GIT_HOOK_EVENTS,
    RUNTIME_HOOK_EVENTS,
    ComponentConfig,
    ComponentType,
    Composition,
    LeftoverSidecarHooksError,
    RuleMetadata,
    RuntimeHook,
    SkillMetadata,
    resolve_namespace,
)
from ai_hats_tracker.models import (  # noqa: F401
    Attachment,
    TaskCard,
    TaskState,
    WorkLogEntry,
)
