"""Integrator config schemas (HATS-863) — one module per config file/domain:
``project`` (ai-hats.yaml), ``user`` (~ai-hats user layer), ``harness``
(channel + feedback), ``overlay`` (per-role customizations), ``migrations``.
Integrator-owned per ADR-0014 §2.
"""

from .harness import (  # noqa: F401
    Channel,
    FeedbackConfig,
    FeedbackPolicy,
    HarnessConfig,
    SessionRetroConfig,
    SmartThreshold,
)
from .overlay import OverlayConfig  # noqa: F401
from .project import (  # noqa: F401
    KNOWN_SCHEMA_VERSION,
    ProjectConfig,
    ProjectConfigError,
    _DEPRECATED_PROJECT_FIELDS,
)
from .user import UserConfig, UserConfigError  # noqa: F401
from .worktree import WorktreeConfig  # noqa: F401
