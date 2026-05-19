"""Project-local cache for update-check results.

Stored at ``<ai_hats_dir>/.cache/update-check.json``. TTL is 24h — within
that window the pipeline step skips the network probe and re-uses the cache.
Stale entries are still returned by :func:`read_cache` (with ``is_fresh =
False``) so the banner step can render even before the background refresh
of the current session completes — stale-while-revalidate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import ai_hats_dir

TTL = timedelta(days=1)


@dataclass(frozen=True)
class CacheEntry:
    checked_at: datetime
    installed_sha: str
    latest_sha: str
    remote_url: str

    @property
    def is_fresh(self) -> bool:
        return (datetime.now(timezone.utc) - self.checked_at) < TTL

    @property
    def has_update(self) -> bool:
        return bool(self.installed_sha and self.latest_sha) and (
            self.installed_sha != self.latest_sha
        )


def cache_path(project_dir: Path) -> Path:
    return ai_hats_dir(project_dir) / ".cache" / "update-check.json"


def _parse_iso(s: str) -> datetime:
    # Accept both ``...Z`` and ``...+00:00`` forms.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def read_cache(project_dir: Path) -> CacheEntry | None:
    """Return the cached entry, or ``None`` when the file is missing/corrupt."""
    p = cache_path(project_dir)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return CacheEntry(
            checked_at=_parse_iso(data["checked_at"]),
            installed_sha=str(data["installed_sha"]),
            latest_sha=str(data["latest_sha"]),
            remote_url=str(data["remote_url"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_cache(project_dir: Path, entry: CacheEntry) -> None:
    p = cache_path(project_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    iso = entry.checked_at.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    payload = {
        "checked_at": iso,
        "installed_sha": entry.installed_sha,
        "latest_sha": entry.latest_sha,
        "remote_url": entry.remote_url,
    }
    p.write_text(json.dumps(payload, indent=2) + "\n")
