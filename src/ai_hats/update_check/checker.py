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

After ``ls-remote`` resolves the upstream SHA, ``run_check`` performs a
single-ref ``git fetch`` into the package checkout so both SHAs are in the
local object graph, then computes ``(ahead, behind)`` via
``git rev-list --left-right --count <installed>...<latest>`` and resolves
human-readable labels via ``git describe --tags``. All of these can fail
silently — the cache then carries ``None`` for the missing fields and
:meth:`CacheEntry.has_update` returns False (banner suppressed).
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
FETCH_TIMEOUT = 10
GIT_QUERY_TIMEOUT = 5


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


def _fetch_into_pkg(remote_url: str, ref: str = "master") -> bool:
    """``git fetch --quiet <url> <ref>`` in the package checkout.

    Brings the upstream ref into the local object graph so subsequent
    ``rev-list`` / ``describe`` against ``<latest_sha>`` resolve locally.
    Silent failure (no remote, network down, not a git checkout) → False;
    the caller treats that as "ahead/behind unknown".
    """
    pkg_dir = _package_dir()
    try:
        result = subprocess.run(
            ["git", "-C", str(pkg_dir), "fetch", "--quiet", remote_url, ref],
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _count_ahead_behind(installed: str, latest: str) -> tuple[int, int] | None:
    """``git rev-list --left-right --count <installed>...<latest>`` → ``(ahead, behind)``.

    Output is ``"<L>\\t<R>"`` where L = commits in installed not in latest
    (ahead-of-upstream), R = commits in latest not in installed (behind).
    Returns ``None`` on any failure (missing object, not a git checkout,
    parse error).
    """
    pkg_dir = _package_dir()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(pkg_dir),
                "rev-list",
                "--left-right",
                "--count",
                f"{installed}...{latest}",
            ],
            capture_output=True,
            text=True,
            timeout=GIT_QUERY_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def _describe(sha: str) -> str | None:
    """``git describe --tags <sha>`` → label or ``None``.

    Best-effort cosmetic label for the banner. Fails (returns ``None``)
    when the repo has no tags, is shallow, or the SHA isn't reachable.
    """
    pkg_dir = _package_dir()
    try:
        result = subprocess.run(
            ["git", "-C", str(pkg_dir), "describe", "--tags", sha],
            capture_output=True,
            text=True,
            timeout=GIT_QUERY_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    label = result.stdout.strip()
    return label or None


def run_check(project_dir: Path) -> CacheEntry | None:
    """Run a full check, persist cache, return the entry.

    Returns ``None`` if either SHA side is unresolved (cache untouched).
    Ahead/behind/labels are best-effort: failures persist as ``None`` in
    the entry and :meth:`CacheEntry.has_update` returns False — the banner
    stays silent rather than firing with stale or unverified state.
    """
    installed = detect_installed_sha()
    if installed is None:
        return None
    remote_url = detect_remote_url()
    latest = fetch_latest_sha(remote_url)
    if latest is None:
        return None

    ahead: int | None = None
    behind: int | None = None
    if _fetch_into_pkg(remote_url):
        counts = _count_ahead_behind(installed, latest)
        if counts is not None:
            ahead, behind = counts

    installed_label = _describe(installed)
    latest_label = _describe(latest)

    entry = CacheEntry(
        checked_at=datetime.now(timezone.utc),
        installed_sha=installed,
        latest_sha=latest,
        remote_url=remote_url,
        behind=behind,
        ahead=ahead,
        installed_label=installed_label,
        latest_label=latest_label,
    )
    write_cache(project_dir, entry)
    return entry
