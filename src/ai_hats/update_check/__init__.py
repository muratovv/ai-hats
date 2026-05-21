"""Update check: detect ai-hats upstream master vs installed SHA, surface banner.

Public API consumed by pipeline steps and the background entry-point. Opt-out
via ``AI_HATS_NO_UPDATE_CHECK`` env var disables both the network probe and
the rendered banner — discoverability is the third dim line of the banner
itself.
"""

from __future__ import annotations

import os

from .cache import CacheEntry, cache_path, read_cache, write_cache
from .checker import (
    detect_installed_sha,
    detect_remote_url,
    fetch_latest_sha,
    run_check,
)

OPT_OUT_ENV = "AI_HATS_NO_UPDATE_CHECK"


def is_disabled() -> bool:
    """True when the user has opted out via ``AI_HATS_NO_UPDATE_CHECK``."""
    return bool(os.environ.get(OPT_OUT_ENV))


__all__ = [
    "CacheEntry",
    "OPT_OUT_ENV",
    "cache_path",
    "detect_installed_sha",
    "detect_remote_url",
    "fetch_latest_sha",
    "is_disabled",
    "read_cache",
    "run_check",
    "write_cache",
]
