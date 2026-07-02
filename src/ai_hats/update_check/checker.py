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

from ..git_env import scrubbed_git_env
from .cache import CacheEntry, write_cache


FALLBACK_REMOTE_URL = "https://github.com/muratovv/ai-hats.git"
LS_REMOTE_TIMEOUT = 10
REV_PARSE_TIMEOUT = 5
FETCH_TIMEOUT = 10
GIT_QUERY_TIMEOUT = 5


def sha_matches(a: str | None, b: str | None) -> bool:
    """Prefix-tolerant SHA equality (HATS-781).

    The update-check cache may hold a 9-char baked short SHA (from
    ``_version.py``, e.g. ``86a6bb1a0``) while :func:`detect_installed_sha`
    returns a full 40-char ``git rev-parse HEAD``. A bare ``==`` would treat
    the same commit as a mismatch and re-probe / suppress on every session.
    Compare via mutual ``startswith`` so a short SHA matches its long form.
    Empty / ``None`` on either side → ``False`` (unknown ≠ match).
    """
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _package_dir() -> Path:
    return Path(ai_hats.__file__).resolve().parent


def _pkg_tracked_by_local_git(pkg_dir: Path) -> bool:
    """True iff ``pkg_dir/__init__.py`` is tracked by the enclosing git repo.

    HATS-441: ``git -C <pkg_dir> ...`` walks up looking for ``.git``. When
    ai_hats is non-editable-installed into
    ``<project>/.venv/.../site-packages/ai_hats/`` and the user's project
    itself is a git repo, the walk finds the *project's* ``.git`` — a
    foreign repository that knows nothing about ai_hats. Without this
    guard:

    - :func:`detect_installed_sha` returns the user's project HEAD as
      ai-hats's installed SHA (wrong),
    - :func:`_fetch_into_pkg` pollutes the user's project repo with
      ai-hats's ``master`` ref (side effect on user data).

    Fast check via ``git ls-files --error-unmatch __init__.py`` — non-zero
    when the file isn't tracked by the found repo. For editable installs
    (``<repo>/src/ai_hats/__init__.py``) the file IS tracked so probe
    paths proceed normally.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(pkg_dir),
             "ls-files", "--error-unmatch", "__init__.py"],
            capture_output=True,
            text=True,
            timeout=REV_PARSE_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def detect_installed_sha() -> str | None:
    """SHA of the installed copy of ai-hats; ``None`` when unknown.

    HATS-441: gates ``git rev-parse HEAD`` behind
    :func:`_pkg_tracked_by_local_git` so a foreign ``.git`` in an ancestor
    of pkg_dir can't masquerade as ai-hats's repo. Falls back to the
    ``__commit__`` baked into ``_version.py`` by setuptools-scm at install
    time for wheel / non-editable scenarios.
    """
    pkg_dir = _package_dir()
    if _pkg_tracked_by_local_git(pkg_dir):
        try:
            result = subprocess.run(
                ["git", "-C", str(pkg_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=REV_PARSE_TIMEOUT,
                check=False,
                env=scrubbed_git_env(),
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                if sha:
                    return sha
        except (FileNotFoundError, subprocess.SubprocessError):
            pass
    return _read_baked_commit_sha()


def _read_baked_commit_sha() -> str | None:
    """Read the installed SHA from ``ai_hats._version`` (HATS-458 fix).

    setuptools-scm 8+ writes ``__commit_id__`` (and ``commit_id``) into the
    generated ``_version.py`` — a short SHA prefixed with ``g`` (the git-
    describe convention, e.g. ``gc1f43bcb6``). Older versions used
    ``__commit__`` without the ``g`` prefix. Strip the prefix when present
    so the returned value is a bare hex SHA (short or full).

    Returns ``None`` when the module is missing all known attribute names
    or all values are the sentinel ``"unknown"``.
    """
    import importlib

    try:
        # importlib.import_module consults sys.modules first — important
        # for unit tests that swap in a synthetic ``_version`` module.
        _version = importlib.import_module("ai_hats._version")
    except ImportError:
        return None
    for attr in ("__commit_id__", "commit_id", "__commit__"):
        value = getattr(_version, attr, None)
        if isinstance(value, str) and value and value != "unknown":
            # ``g<sha>`` → ``<sha>`` (setuptools-scm describe prefix).
            return value[1:] if value.startswith("g") else value
    return None


def _coerce_to_https(url: str) -> str:
    """Map a git+ssh URL form to https so ``git ls-remote`` works without keys.

    The default is git+https (HATS-766); an ``AI_HATS_REPO_URL`` override may
    still carry ``git+ssh://`` (HATS-337) — the probe only needs the bare https.
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


def fetch_latest_sha(remote_url: str, ref: str = "master") -> str | None:
    """``git ls-remote <url> <ref>`` → SHA. ``None`` on network/timeout/error.

    ``ref`` defaults to ``master`` (banner); the edge guard passes ``HEAD`` to
    probe a custom repo's own default branch (HATS-766). ``git+`` prefix stripped.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", remote_url.removeprefix("git+"), ref],
            capture_output=True,
            text=True,
            timeout=LS_REMOTE_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
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

    HATS-441: gated by :func:`_pkg_tracked_by_local_git` to prevent
    polluting a foreign user-project ``.git`` with our remote refs.
    """
    pkg_dir = _package_dir()
    if not _pkg_tracked_by_local_git(pkg_dir):
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(pkg_dir), "fetch", "--quiet",
             remote_url.removeprefix("git+"), ref],
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _probe_mirror_dir(project_dir: Path) -> Path:
    """Path to the bare probe-mirror used as the local object graph for
    non-editable / wheel installs (HATS-458)."""
    from ..paths import ai_hats_dir

    return ai_hats_dir(project_dir) / ".cache" / "probe-mirror"


def _ensure_probe_mirror(project_dir: Path) -> Path | None:
    """Init or reuse a bare probe-mirror at ``<ai_hats_dir>/.cache/probe-mirror/``.

    HATS-458: when ``_fetch_into_pkg`` refuses (no usable ``.git`` next to
    the installed package — the common non-editable layout), the mirror is
    the local object graph in which we fetch upstream master + installed
    SHA, then run ``rev-list`` / ``describe`` against it. Idempotent —
    existing mirror (``HEAD`` file present) is reused.

    Returns the mirror path on success, ``None`` when ``git init`` failed
    (no git, no write permission, etc.).
    """
    mirror = _probe_mirror_dir(project_dir)
    if (mirror / "HEAD").exists():
        return mirror
    try:
        mirror.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    try:
        result = subprocess.run(
            ["git", "init", "--bare", "--quiet", str(mirror)],
            capture_output=True,
            text=True,
            timeout=GIT_QUERY_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return mirror if result.returncode == 0 else None


def _fetch_into_mirror(mirror: Path, remote_url: str, ref: str) -> bool:
    """``git fetch <remote_url> <ref>`` into the probe-mirror (HATS-458).

    Full fetch (no shallow). The ai-hats default branch is small
    (hundreds of commits, a few hundred KB), so fetching it fully is
    cheap and guarantees ``rev-list installed...<latest>`` resolves
    correctly — the typical non-editable user's installed_sha is some
    ancestor of the probed ref and thus already in the local object graph
    after the fetch. No separate ``fetch <installed_sha>`` call
    is needed; that avoids the short-SHA / protocol limitations of
    fetch-by-SHA (setuptools-scm bakes a 9-char short SHA into
    ``_version.py`` which most HTTPS remotes refuse in the want line).

    Concurrency: relies on git's own ref-level locking. A concurrent
    probe racing the same fetch may surface a transient lock failure →
    False; the next probe re-fetches and heals.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(mirror), "fetch",
             "--quiet", remote_url.removeprefix("git+"), ref],
            capture_output=True,
            text=True,
            timeout=FETCH_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _count_ahead_behind(
    installed: str,
    latest: str,
    *,
    git_dir: Path | None = None,
) -> tuple[int, int] | None:
    """``git rev-list --left-right --count <installed>...<latest>`` → ``(ahead, behind)``.

    Output is ``"<L>\\t<R>"`` where L = commits in installed not in latest
    (ahead-of-upstream), R = commits in latest not in installed (behind).
    Returns ``None`` on any failure (missing object, not a git checkout,
    parse error).

    HATS-458: ``git_dir`` selects which repository hosts the rev-list.
    Default is the package directory (editable / git-checkout path);
    pass the probe-mirror returned by :func:`_ensure_probe_mirror` for
    non-editable installs.
    """
    target = git_dir if git_dir is not None else _package_dir()
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "rev-list",
                "--left-right",
                "--count",
                f"{installed}...{latest}",
            ],
            capture_output=True,
            text=True,
            timeout=GIT_QUERY_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
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


def _describe(sha: str, *, git_dir: Path | None = None) -> str | None:
    """``git describe --tags <sha>`` → label or ``None``.

    Best-effort cosmetic label for the banner. Fails (returns ``None``)
    when the repo has no tags, is shallow, or the SHA isn't reachable.

    HATS-458: ``git_dir`` selects which repository hosts the describe.
    Mirror fetches use ``--depth=50`` so older tags may be unreachable —
    that's acceptable; the banner falls back to short SHAs.
    """
    target = git_dir if git_dir is not None else _package_dir()
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "describe", "--tags", sha],
            capture_output=True,
            text=True,
            timeout=GIT_QUERY_TIMEOUT,
            check=False,
            env=scrubbed_git_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    label = result.stdout.strip()
    return label or None


def run_check(
    project_dir: Path,
    *,
    remote_url: str | None = None,
    ref: str = "master",
) -> CacheEntry | None:
    """Run a full check, persist cache, return the entry.

    Returns ``None`` if either SHA side is unresolved (cache untouched).
    Ahead/behind/labels are best-effort: failures persist as ``None`` in
    the entry and :meth:`CacheEntry.has_update` returns False — the banner
    stays silent rather than firing with stale or unverified state.

    HATS-766: ``remote_url`` / ``ref`` override the probed target (banner uses the
    defaults; the edge guard passes a bare edge URL + ``HEAD``). ``remote_url``
    must be bare — the git helpers strip ``git+`` defensively.
    """
    installed = detect_installed_sha()
    if installed is None:
        return None
    if remote_url is None:
        remote_url = detect_remote_url()
    latest = fetch_latest_sha(remote_url, ref)
    if latest is None:
        return None

    ahead: int | None = None
    behind: int | None = None
    installed_label: str | None = None
    latest_label: str | None = None

    # Fast path: pkg-checkout (editable installs reachable through
    # ``_fetch_into_pkg``'s tracked-check gate, HATS-441).
    if _fetch_into_pkg(remote_url, ref):
        counts = _count_ahead_behind(installed, latest)
        if counts is not None:
            ahead, behind = counts
        installed_label = _describe(installed)
        latest_label = _describe(latest)
    else:
        # HATS-458 fallback: probe-mirror for non-editable / wheel
        # installs. We own a bare repo and fetch both refs explicitly so
        # ``rev-list`` / ``describe`` resolve locally — no pollution of
        # any foreign ``.git`` in an ancestor (HATS-441 closed that
        # surface), no dependency on a pkg-checkout-shaped install.
        mirror = _ensure_probe_mirror(project_dir)
        if (
            mirror is not None
            and _fetch_into_mirror(mirror, remote_url, ref)
        ):
            counts = _count_ahead_behind(installed, latest, git_dir=mirror)
            if counts is not None:
                ahead, behind = counts
            installed_label = _describe(installed, git_dir=mirror)
            latest_label = _describe(latest, git_dir=mirror)

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
