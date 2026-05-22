"""Project-local cache for update-check results.

Stored at ``<ai_hats_dir>/.cache/update-check.json``. TTL is 24h — within
that window the pipeline step skips the network probe and re-uses the cache.
Stale entries are still returned by :func:`read_cache` (with ``is_fresh =
False``) so the banner step can render even before the background refresh
of the current session completes — stale-while-revalidate.

The schema carries ``behind`` / ``ahead`` counts (from
``git rev-list --left-right --count <installed>...<latest>`` at probe time)
plus optional human-readable ``installed_label`` / ``latest_label``
(``git describe --tags``). ``has_update`` is True only when installed is
strictly behind upstream — closes the false-positive class where the
installed HEAD is *ahead* of the cached upstream SHA (HATS-432).
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
    # Ahead/behind counts from ``git rev-list --left-right --count``.
    # ``None`` means git ops failed at probe (non-git install, shallow,
    # fetch failure) — ``has_update`` returns False so no false-positive
    # banner fires.
    behind: int | None = None
    ahead: int | None = None
    # ``git describe --tags <sha>`` for each side; cosmetic banner labels.
    # ``None`` when describe is unavailable (no tags / shallow / non-git).
    installed_label: str | None = None
    latest_label: str | None = None

    @property
    def is_fresh(self) -> bool:
        return (datetime.now(timezone.utc) - self.checked_at) < TTL

    @property
    def has_update(self) -> bool:
        """True only when installed is strictly behind upstream.

        Closes every false-positive class shipped by the original
        ``installed_sha != latest_sha`` check (installed-ahead, diverged,
        identical-but-unequal-strings).
        """
        return (
            self.behind is not None
            and self.ahead is not None
            and self.behind > 0
            and self.ahead == 0
        )


def cache_path(project_dir: Path) -> Path:
    return ai_hats_dir(project_dir) / ".cache" / "update-check.json"


def _parse_iso(s: str) -> datetime:
    # Accept both ``...Z`` and ``...+00:00`` forms.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def _opt_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def _opt_str(v: object) -> str | None:
    if isinstance(v, str) and v:
        return v
    return None


def read_cache(project_dir: Path) -> CacheEntry | None:
    """Return the cached entry, or ``None`` when the file is missing/corrupt.

    Forward-compatible read: legacy entries (no ``behind`` / ``ahead`` /
    labels) parse cleanly — the missing fields default to ``None`` and
    ``has_update`` returns False until the next probe overwrites the cache
    with the new schema.
    """
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
            behind=_opt_int(data.get("behind")),
            ahead=_opt_int(data.get("ahead")),
            installed_label=_opt_str(data.get("installed_label")),
            latest_label=_opt_str(data.get("latest_label")),
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
        "behind": entry.behind,
        "ahead": entry.ahead,
        "installed_label": entry.installed_label,
        "latest_label": entry.latest_label,
    }
    p.write_text(json.dumps(payload, indent=2) + "\n")
