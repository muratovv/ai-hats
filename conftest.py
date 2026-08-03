"""Repo-root fixtures — the ones that must reach every tree in ``testpaths``.

A conftest binds only to its own subtree, so ``tests/conftest.py`` never
reached ``packages/*/tests``, whose worktree-creating tests leaked
``ai-hats-wt-*`` into the real temp root (HATS-570).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.dont_write_bytecode = True


# Enables `pytester` for test_tmp_hygiene.py; pytest refuses this in a
# non-top-level conftest, so it can only live here (HATS-570).
pytest_plugins = ["pytester"]


def _ambient_cache_home() -> Path:
    """The cache root the DEVELOPER's machine resolves, read once at import.

    Mirrors ``ai_hats.paths.cache_home`` deliberately instead of calling it: the
    live resolver reads the env, and by the time any test runs that env points at
    the sandbox pinned below — a tripwire asking it could never fire (HATS-1372).
    """
    raw = os.environ.get("AI_HATS_CACHE_HOME")
    if raw:
        return Path(raw).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "ai-hats"
    return Path.home() / ".cache" / "ai-hats"


REAL_CACHE_HOME = _ambient_cache_home()
_leaked_keys: dict[str, tuple[str, Path | None]] = {}
_SKIP_DIRS = frozenset({".git", ".venv", "site-packages", "node_modules", "__pycache__"})


def _cache_keys() -> set[str]:
    try:
        return {entry.name for entry in REAL_CACHE_HOME.iterdir()}
    except OSError:
        return set()


def _project_key(project_dir: Path) -> str:
    """Copy of ``ai_hats.paths.project_key`` — see :func:`_ambient_cache_home`."""
    import hashlib

    resolved = project_dir.expanduser().resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
    slug = "".join(c if (c.isalnum() or c in "._-") else "-" for c in resolved.name)
    return f"{slug.strip('-.') or 'project'}-{digest}"


def _source_dir(key: str, root: Path, max_depth: int = 5) -> Path | None:
    """Find the directory under ``root`` whose project key is ``key``.

    This is what separates OUR leak from a concurrent ai-hats session writing to
    the same shared cache: the key is a one-way hash, so the only sound proof of
    authorship is a source directory inside this run's own tmp tree.
    """
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = [entry for entry in current.iterdir() if entry.is_dir()]
        except OSError:
            continue
        for child in children:
            if child.name in _SKIP_DIRS:
                continue
            if _project_key(child) == key:
                return child
            if depth < max_depth:
                stack.append((child, depth + 1))
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Attribute each new real-cache key to the test that produced it (HATS-1473).

    Attribution happens here, not at session end, because the source directory a
    key is matched against is usually deleted by the time the session finishes.
    """
    before = _cache_keys()
    yield
    new = _cache_keys() - before
    if not new:
        return
    basetemp = item.config._tmp_path_factory.getbasetemp()
    for key in new:
        _leaked_keys.setdefault(key, (item.nodeid, _source_dir(key, basetemp)))


@pytest.fixture(scope="session", autouse=True)
def _cache_home_sandbox(tmp_path_factory):
    """Pin ``AI_HATS_CACHE_HOME`` into a session-owned sandbox (HATS-1473).

    Session scope on purpose: ``_shared_launcher_venv`` builds a real launcher
    during the FIRST test's setup and captures ``os.environ`` there, so a
    function-scoped pin lands too late for it — that is how ``bootstrap-*`` keys
    (5.1 GB of bare repo mirrors) reached the developer's cache. Pinning here
    also covers ``packages/*/tests``, which no ``tests/conftest.py`` fixture ever
    reached. Same shape as :func:`_wt_sandbox` above, for the same reason.
    """
    sandbox = tmp_path_factory.mktemp("cache-home")
    mp = pytest.MonkeyPatch()
    mp.setenv("AI_HATS_CACHE_HOME", str(sandbox))
    mp.delenv("XDG_CACHE_HOME", raising=False)
    try:
        yield sandbox
    finally:
        mp.undo()


@pytest.fixture(scope="session", autouse=True)
def _real_cache_home_tripwire():
    """Fail the session if any test wrote a project key into the real cache home.

    HATS-1473: a full e2e run left 123 fixture keys in the developer's cache
    (16.9 GB accumulated). HATS-1398 fixed the sites it knew about; nothing went
    red when the next fixture reopened the hole. This is what goes red.

    Fails only on keys traced to a source dir inside this run: a dev box runs
    several ai-hats sessions against this shared cache, so failing on every new
    key would be flaky. Unowned keys are reported — the limit of the measurement.
    """
    before = _cache_keys()
    yield
    new = _cache_keys() - before
    if not new:
        return
    owned, foreign = [], []
    for key in sorted(new):
        nodeid, source = _leaked_keys.get(key, ("<outside any test>", None))
        (owned if source else foreign).append(f"{key}  <- {nodeid}  ({source})")
    if foreign:
        print(
            f"\n[cache-home] {len(foreign)} key(s) appeared in {REAL_CACHE_HOME} with no "
            "source dir in this run — most likely a concurrent ai-hats session:\n  "
            + "\n  ".join(foreign)
        )
    if owned:
        pytest.fail(
            f"[cache-home] {len(owned)} project key(s) leaked into the real "
            f"{REAL_CACHE_HOME} (HATS-1473):\n  " + "\n  ".join(owned),
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def _wt_sandbox(tmp_path_factory, request):
    """Redirect every ``ai-hats-wt-*`` worktree birth into a session-owned
    sandbox so they never pollute the real temp root (HATS-570).

    setup    : a fresh sandbox — self-heals any leftovers a prior crashed
               run might have left (cleanup is idempotent by construction).
    teardown : ``rmtree`` ONLY on a fully-green session. A session with
               failures keeps the sandbox and prints its path so the
               worktree artefacts survive for triage.

    Redirects BOTH worktree creation paths:

    * in-process ``mgr.create()`` — ``tempfile.gettempdir()`` memoises
      into ``tempfile.tempdir`` on first use, so patching that cached
      attribute is REQUIRED; ``setenv`` alone would be too late.
    * subprocess CLI ``wt create`` — tests run ``env = os.environ.copy()``
      so ``TMPDIR`` rides along into the child's ``mkdtemp``.
    """  # comment-length: allow — moved verbatim; names both redirect paths
    sandbox = tmp_path_factory.mktemp("wt-sandbox")
    mp = pytest.MonkeyPatch()
    mp.setattr(tempfile, "tempdir", str(sandbox))
    mp.setenv("TMPDIR", str(sandbox))
    try:
        yield sandbox
    finally:
        mp.undo()
        if request.session.testsfailed == 0:
            shutil.rmtree(sandbox, ignore_errors=True)
        else:
            print(
                f"\n[wt-sandbox] {request.session.testsfailed} failure(s) — "
                f"worktree artefacts preserved for triage: {sandbox}"
            )
