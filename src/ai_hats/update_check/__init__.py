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


def upstream_update(project_dir: Path) -> CacheEntry | None:
    """The cache entry iff the *running* build is genuinely behind upstream, else None.

    The one canonical reader of the behind signal (HATS-846): bundles
    is_local_channel (LOCAL is git-driven, never "behind") + has_update +
    running-SHA sha_matches, so the banner and hook self-heal can't diverge on the
    guard set (a guard-test pins the signal to a single reader). is_disabled is NOT
    folded in (it suppresses the notification, not hook-safety); an unknown running
    SHA is treated as about-us, not suppressed.
    """
    if is_local_channel(project_dir):
        return None
    entry = read_cache(project_dir)
    if entry is None or not entry.has_update:
        return None
    current = detect_installed_sha()
    if current is not None and not sha_matches(entry.installed_sha, current):
        return None
    return entry


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
    "upstream_update",
    "write_cache",
]
