"""Repo-root fixtures — the ones that must reach every tree in ``testpaths``.

A conftest binds only to its own subtree, so ``tests/conftest.py`` never
reached ``packages/*/tests``, whose worktree-creating tests leaked
``ai-hats-wt-*`` into the real temp root (HATS-570).
"""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

# HATS-1622: an inherited identity hands a throwaway tmp project the developer's
# role, whose bindings then refuse every transition (D9 clause 4). Dropped at
# import, as a unit — the envelope alone leaves a half-session `from_env` rejects.
from ai_hats.session_identity import drop_identity  # noqa: E402

drop_identity(os.environ)


# Enables `pytester` for test_tmp_hygiene.py; pytest refuses this in a
# non-top-level conftest, so it can only live here (HATS-570).
pytest_plugins = ["pytester"]

#: Read before any test can chdir away — the one dir known to outlive them all.
_SESSION_CWD = os.getcwd()


def _sigterm_as_keyboard_interrupt(signum, _frame):
    raise KeyboardInterrupt(f"pytest terminated by signal {signum}")


def pytest_configure(config):
    """Route SIGTERM into pytest's own interrupt path (HATS-1663).

    A run the harness kills at its ceiling would otherwise die without
    unwinding, skipping the two session tripwires that are the only check that a
    test wrote to the real checkout. KeyboardInterrupt reaches teardown and
    keeps the exit at ``ExitCode.INTERRUPTED`` — a killed run must never be able
    to report green.
    """
    signal.signal(signal.SIGTERM, _sigterm_as_keyboard_interrupt)
    _tier_memo_open(config)


#: Where the gate keeps what this tree has already proven, if a gate is running
#: us. ``scripts/gates.sh`` sets it for the stages that PARTITION the e2e tier
#: and for nothing else.
TIER_MEMO_ENV = "AI_HATS_GATE_TIER_MEMO"
#: The key the controller hands its xdist workers the same path under.
TIER_MEMO_WORKERINPUT = "ai_hats_tier_memo"
_tier_memo_path_for_this_run: Path | None = None
_tier_memo_seen: set[str] = set()
_tier_memo_sink = None
_tier_memo_deselected = 0


def _tier_memo_path(config) -> Path | None:
    """The memo THIS run may use, or ``None``.

    Taken OUT of the environment rather than read from it: a test of ours may
    spawn pytest again — the hermetic unit gate does, twice — and an inherited
    memo made that nested run write its own node ids into a gate's record and
    the next one skip them, so the gate test failed for a reason that had
    nothing to do with the gate. The stage handed the memo is the only run it
    describes, so this process consumes the variable and its children see none.
    An xdist worker is the exception: it gets the path by the hook below,
    because the controller consumed it before any worker existed.

    Never under ``--collect-only``: the partition laws are asked by collecting
    each stage, and a memo answering there would shrink the sets that must add
    up to the whole tier — the laws would go green while the tier lost tests.
    """
    worker = getattr(config, "workerinput", None)
    raw = worker.get(TIER_MEMO_WORKERINPUT) if worker else os.environ.pop(TIER_MEMO_ENV, None)
    if not raw or config.option.collectonly:
        return None
    return Path(raw)


def pytest_configure_node(node):
    """Hand an xdist worker the memo its controller consumed.

    A worker that did not deselect what its siblings deselected would make the
    two collections differ, which xdist refuses outright."""
    if _tier_memo_path_for_this_run is not None:
        node.workerinput[TIER_MEMO_WORKERINPUT] = str(_tier_memo_path_for_this_run)


def _tier_memo_open(config) -> None:
    """Read what this tree has passed, and open the sink for what it passes now.

    Overlapping zones are the point: one gate run can name two stages that share
    tests, and the test is the same test on the same tree. It runs in the first
    and is deselected in the second.
    """
    global _tier_memo_sink, _tier_memo_path_for_this_run
    memo = _tier_memo_path(config)
    if memo is None:
        return
    _tier_memo_path_for_this_run = memo
    if memo.exists():
        try:
            _tier_memo_seen.update(memo.read_text(encoding="utf-8").split())
        except OSError as exc:
            warnings.warn(
                f"tier memo {memo} unreadable ({exc!r}) — running all of it", stacklevel=1
            )
    # xdist: reports reach the controller, so the controller alone writes. A
    # worker opening this too would interleave lines into the same file.
    if hasattr(config, "workerinput"):
        return
    try:
        memo.parent.mkdir(parents=True, exist_ok=True)
        _tier_memo_sink = memo.open("a", encoding="utf-8", buffering=1)
    except OSError as exc:
        warnings.warn(f"tier memo {memo} not writable ({exc!r}) — recording nothing", stacklevel=1)


def pytest_collection_modifyitems(config, items):
    """Drop what this tree has already passed under another stage's name."""
    global _tier_memo_deselected
    if _tier_memo_path_for_this_run is None or not _tier_memo_seen:
        return
    keep = [item for item in items if item.nodeid not in _tier_memo_seen]
    dropped = [item for item in items if item.nodeid in _tier_memo_seen]
    if not dropped:
        return
    _tier_memo_deselected += len(dropped)
    config.hook.pytest_deselected(items=dropped)
    items[:] = keep


def pytest_runtest_logreport(report):
    """Record a test that PASSED, and only that.

    A failure left in the memo would let the next stage report green for the red
    it never ran — the one way this optimisation could lie."""
    if _tier_memo_sink is None or report.when != "call" or not report.passed:
        return
    _tier_memo_sink.write(f"{report.nodeid}\n")


def pytest_sessionfinish(session, exitstatus):
    """A stage whose every test was already green on this tree ran nothing, and
    pytest spells that 5. It is not a failure here: the tests exist, they passed,
    and the stage is about to be stamped for the same tree they passed on."""
    if _tier_memo_deselected and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = pytest.ExitCode.OK


def pytest_unconfigure(config):
    global _tier_memo_sink
    if _tier_memo_sink is not None:
        _tier_memo_sink.close()
        _tier_memo_sink = None


@pytest.fixture(autouse=True)
def _surviving_cwd():
    """Keep one test's chdir from taking the rest of the session with it.

    A test that chdirs into its own ``tmp_path`` leaves the process standing in
    a deleted directory once ``tmp_path_retention_policy=failed`` reaps it, and
    from then on every ``os.getcwd()`` raises ``FileNotFoundError`` — including
    the one inside ``monkeypatch.chdir``. Measured: 3 such tests cost 59
    failures and 933 errors, and the first one reported was an unrelated test
    several files away (HATS-1624). Autouse at the root, so this teardown runs
    after the per-test finalizers that do the deleting.
    """  # comment-length: allow — the cascade is why a warning beats a repair
    yield
    try:
        os.getcwd()
    except OSError:
        os.chdir(_SESSION_CWD)
        warnings.warn(
            "test left the process in a deleted directory; cwd restored to "
            f"{_SESSION_CWD}. Use `monkeypatch.chdir` rather than `os.chdir`.",
            stacklevel=1,
        )


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


_KEYS_BEFORE = pytest.StashKey[set]()


def _attribute_new_keys(item) -> None:
    """Match this test's new cache keys to a source dir, first writer wins.

    ``setdefault`` is what makes the double call safe: the teardown-time pass
    below runs first and carries the source dir, so the protocol-time pass can
    only add keys it did not already see.
    """
    before = item.stash.get(_KEYS_BEFORE, None)
    if before is None:
        return
    new = _cache_keys() - before
    if not new:
        return
    basetemp = item.config._tmp_path_factory.getbasetemp()
    for key in new:
        _leaked_keys.setdefault(key, (item.nodeid, _source_dir(key, basetemp)))


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    """Attribute BEFORE any finalizer runs — both readers of the evidence die here.

    A key is only reportable while its source dir is on disk and while something
    is still left to report to, and teardown is where both end (HATS-1624):
    ``tmp_path_retention_policy=failed`` rmtrees the source dir in its finalizer,
    and on the LAST test the session-scoped tripwire is finalized here too, so an
    attribution written after the protocol arrives past its only reader.
    """  # comment-length: allow — the ordering IS the contract this hook enforces
    _attribute_new_keys(item)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Attribute each new real-cache key to the test that produced it (HATS-1473).

    Attribution happens here, not at session end, because the source directory a
    key is matched against is usually deleted by the time the session finishes.
    This pass is the backstop for keys born during teardown, after the hook
    above has run; the source dir may already be gone for those.
    """
    item.stash[_KEYS_BEFORE] = _cache_keys()
    yield
    _attribute_new_keys(item)


@pytest.fixture(scope="session", autouse=True)
def _cache_home_sandbox(tmp_path_factory):
    """Pin ``AI_HATS_CACHE_HOME`` into a sandbox private to THIS run (HATS-1473).

    ``mktemp`` makes the dir unique per session, so concurrent runs never share a
    cache root; ``tests/conftest.py`` narrows it again per test. Stale sandboxes
    are the product's job — it sweeps orphaned keys by TTL.

    Session scope on purpose: ``_shared_launcher_venv`` captures ``os.environ``
    during the FIRST test's setup, so a function-scoped pin lands too late for it
    — that is how ``bootstrap-*`` keys (5.1 GB of bare repo mirrors) reached the
    developer's cache. Pinning here also covers ``packages/*/tests``, which no
    ``tests/conftest.py`` fixture ever reached.
    """  # comment-length: allow — names both the scope trap and the coverage gap
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

    The pin above is the guarantee; this is what makes it falsifiable. HATS-1398
    fixed the leaking sites it knew about, nothing went red when the next fixture
    reopened the hole, and 16.9 GB accumulated.

    Fails only on keys traced to a source dir inside this run — other ai-hats
    sessions write to this same cache, and their keys are neither preventable nor
    actionable from here, so they are skipped silently rather than reported.
    """
    before = _cache_keys()
    yield
    owned = []
    for key in sorted(_cache_keys() - before):
        nodeid, source = _leaked_keys.get(key, ("<outside any test>", None))
        if source:
            owned.append(f"{key}  <- {nodeid}  ({source})")
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

    Since HATS-1632 the ai-hats road no longer lands here at all — it mints under
    ``worktree_checkouts_dir``, sandboxed by ``_cache_home_sandbox`` and policed
    by ``_real_cache_home_tripwire``. What is left for this fixture is the
    bare-core fallback (D9: no injected root, so ``mkdtemp``'s temp root), which
    the ``ai-hats-wt`` package suite exercises directly.
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
