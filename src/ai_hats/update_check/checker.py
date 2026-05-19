"""Detect installed/remote SHA for the ai-hats update check.

Two-axis resolution per plan:
- **Installed SHA**: ``git rev-parse HEAD`` in the package directory (works
  for editable / git-checkout installs). Falls back to
  ``ai_hats._version.__commit__`` when ``.git`` is absent (wheel installs).
- **Remote URL**: ``importlib.metadata`` ``Project-URL`` field — first match
  among ``Source`` / ``Repository`` / ``Homepage``. Falls back to a hardcoded
  upstream URL. The ``AI_HATS_REPO_URL`` env var (already consumed by ``self
  update``) overrides everything, so a single var pins the install source
  end-to-end.

``fetch_latest_sha`` invokes ``git ls-remote <url> master`` with a 10s
timeout — silent failure on network/timeout, the caller treats ``None`` as
"unknown, skip".
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path

import ai_hats

from .cache import CacheEntry, write_cache


FALLBACK_REMOTE_URL = "https://github.com/muratovv/ai-hats.git"
LS_REMOTE_TIMEOUT = 10
REV_PARSE_TIMEOUT = 5


def _package_dir() -> Path:
    return Path(ai_hats.__file__).resolve().parent


def detect_installed_sha() -> str | None:
    """SHA of the installed copy of ai-hats; ``None`` when unknown."""
    pkg_dir = _package_dir()
    try:
        result = subprocess.run(
            ["git", "-C", str(pkg_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=REV_PARSE_TIMEOUT,
            check=False,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            if sha:
                return sha
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        from ai_hats._version import __commit__  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        return None
    if isinstance(__commit__, str) and __commit__ and __commit__ != "unknown":
        return __commit__
    return None


def _coerce_to_https(url: str) -> str:
    """Map a git+ssh URL form to https so ``git ls-remote`` works without keys.

    The ``self update`` flow uses ``git+ssh://git@github.com/...`` (HATS-337);
    for an anonymous probe we only need the public https form.
    """
    prefixes = ("git+ssh://git@", "git+https://", "git+")
    for p in prefixes:
        if url.startswith(p):
            url = url[len(p):]
            break
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:"):]
    if url.startswith("github.com/"):
        url = "https://" + url
    return url


def detect_remote_url() -> str:
    """Resolve the upstream repo URL — env override > metadata > fallback."""
    env_url = os.environ.get("AI_HATS_REPO_URL")
    if env_url and "://" in env_url:
        return _coerce_to_https(env_url)
    try:
        meta = metadata("ai-hats")
        for entry in meta.get_all("Project-URL") or []:
            label, _, url = entry.partition(",")
            if label.strip().lower() in {"source", "repository", "homepage"}:
                u = url.strip()
                if u:
                    return u
    except (PackageNotFoundError, KeyError):
        pass
    return FALLBACK_REMOTE_URL


def fetch_latest_sha(remote_url: str) -> str | None:
    """``git ls-remote <url> master`` → SHA. ``None`` on network/timeout/error."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote_url, "master"],
            capture_output=True,
            text=True,
            timeout=LS_REMOTE_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not line:
        return None
    parts = line.split()
    return parts[0] if parts and parts[0] else None


def run_check(project_dir: Path) -> CacheEntry | None:
    """Run a full check, persist cache, return the entry.

    Returns ``None`` if either side is unresolved (cache untouched). All
    network/subprocess errors are absorbed by the leaf calls — this function
    itself doesn't raise.
    """
    installed = detect_installed_sha()
    if installed is None:
        return None
    remote_url = detect_remote_url()
    latest = fetch_latest_sha(remote_url)
    if latest is None:
        return None
    entry = CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha=installed,
        latest_sha=latest,
        remote_url=remote_url,
    )
    write_cache(project_dir, entry)
    return entry
