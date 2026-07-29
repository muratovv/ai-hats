"""Repo-root fixtures — the ones that must reach every tree in ``testpaths``.

A conftest binds only to its own subtree, so ``tests/conftest.py`` never
reached ``packages/*/tests``, whose worktree-creating tests leaked
``ai-hats-wt-*`` into the real temp root (HATS-570).
"""

from __future__ import annotations

import shutil
import sys
import tempfile

import pytest

sys.dont_write_bytecode = True


# Enables `pytester` for test_tmp_hygiene.py; pytest refuses this in a
# non-top-level conftest, so it can only live here (HATS-570).
pytest_plugins = ["pytester"]


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
