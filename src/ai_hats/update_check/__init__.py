"""Update check: detect ai-hats upstream master vs installed SHA, surface banner.

Public API consumed by pipeline steps and the background entry-point. Opt-out
via ``AI_HATS_NO_UPDATE_CHECK`` env var disables both the network probe and
the rendered banner — discoverability is the third dim line of the banner
itself.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

from .cache import CacheEntry, cache_path, read_cache, write_cache
from .checker import (
    detect_installed_sha,
    detect_remote_url,
    fetch_latest_sha,
    run_check,
    sha_matches,
)

OPT_OUT_ENV = "AI_HATS_NO_UPDATE_CHECK"


def is_disabled() -> bool:
    """True when the user has opted out via ``AI_HATS_NO_UPDATE_CHECK``."""
    return bool(os.environ.get(OPT_OUT_ENV))


def is_local_channel(project_dir: Path) -> bool:
    """True iff the harness channel for ``project_dir`` is ``LOCAL`` (HATS-781).

    A LOCAL build is an editable checkout external to the consuming repo —
    the developer drives updates with ``git``, not ``ai-hats self update``, so
    the update banner must be hidden and the background probe skipped.

    Defensive: a missing or unparseable ``ai-hats.yaml`` → ``False`` (do not
    suppress; degrade to the normal banner path). The parse's WARNs are
    swallowed — this is a best-effort session-end read, not the authoritative
    config load.
    """
    import yaml

    from ..models import Channel, ProjectConfig, ProjectConfigError

    config_path = project_dir / "ai-hats.yaml"
    if not config_path.exists():
        return False
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            channel = ProjectConfig.from_yaml(config_path).harness.channel
    except (ProjectConfigError, OSError, ValueError, yaml.YAMLError):
        return False
    return channel == Channel.LOCAL


__all__ = [
    "CacheEntry",
    "OPT_OUT_ENV",
    "cache_path",
    "detect_installed_sha",
    "detect_remote_url",
    "fetch_latest_sha",
    "is_disabled",
    "is_local_channel",
    "read_cache",
    "run_check",
    "sha_matches",
    "write_cache",
]
