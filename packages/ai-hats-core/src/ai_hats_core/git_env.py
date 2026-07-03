"""GIT_* plumbing-env hygiene for cwd-scoped git subprocesses (HATS-890).

An ambient ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` (exported by an
outer merge / rebase-todo / git-hook / git-alias context) overrides git's
cwd-based repo discovery and retargets a ``cwd``-scoped ``git`` subprocess onto
the wrong ``.git``. :func:`scrubbed_git_env` copies ``os.environ`` minus those
three plumbing vars, preserving identity vars (``GIT_AUTHOR_*`` etc.) so
merge-commit identity is unaffected. Extracted to core in HATS-862; the ``wt``
package keeps its own copy until HATS-909 consolidates it.
"""

from __future__ import annotations

import os

_PLUMBING_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def scrubbed_git_env() -> dict[str, str]:
    """Return an ``os.environ`` copy with the 3 GIT_* plumbing vars removed."""
    env = dict(os.environ)
    for var in _PLUMBING_VARS:
        env.pop(var, None)
    return env
