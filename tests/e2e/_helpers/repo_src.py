"""Per-xdist-worker private repo source for in-tree wheel builds (HATS-589).

PITFALL 1 (HATS-589 / HATS-568): ``pip install <local-repo>`` builds the
wheel **in-tree** — pip 21.3+ defaults to in-tree builds and there is no
flag to revert it. The wheel lands in ``<repo>/build/bdist.*`` +
``<repo>/build/lib``, whose paths are version/platform-derived, NOT
process-unique. Under ``pytest -n>1`` every worker that runs
``ai-hats self update`` (or the shared-venv build) against the single
shared checkout writes into the same ``<repo>/build/`` concurrently →
the ``[Errno 17] File exists: build/bdist...dist-info`` collision class.

:func:`build_src` resolves the install source to a private ``git clone
--shared`` of ``repo_root``, ALWAYS — once per process, keyed by worker name
so xdist gets one clone each and a serial run gets one of its own.
``--shared`` references the source object store (no object copy → fast); only
the working tree is materialised. Each clone owns its own ``build/`` → no
cross-worker race. The wheel build needs only the materialised working tree,
so the borrowed object store matters solely *during* the clone — the
guarantee assumes no ``git gc`` on the source repo in that narrow window
(always true for a worktree under an active test session).

PITFALL 2 (HATS-1651): a clone materialises COMMITTED content, so this tier
never sees uncommitted work. A fix edited but not committed is invisible here,
and the run reports the behaviour of ``HEAD`` — commit before running e2e, or
read the result as a statement about the last commit.

Serial used to short-circuit to ``repo_root``, on the argument that one
process cannot race itself. HATS-1560 removed that: building in-tree wrote
``build/`` and ``.pyc`` into the developer's own checkout. Cloning
unconditionally is what keeps the tier out of the working tree — do not
re-add the short-circuit to make uncommitted code visible.

Workers are separate processes (execnet), so the module-level cache is
naturally per-worker and single-threaded — no lock needed. The clone
lives under ``tempfile`` (sandboxed into the pytest temp root by
``tests/conftest.py::_wt_sandbox``), so it is swept with the rest of the
worktree artefacts on a green session.

Deliberate long pitfall/contract module docstring — noqa: comment-length.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

# Per-process (= per-xdist-worker) memo. Key is constant: one clone per worker.
_CACHE: dict[str, Path] = {}

# A --shared clone copies no objects, so this bounds a hang, not the happy path.
CLONE_TIMEOUT_S = 120


def build_src(repo_root: Path) -> Path:
    """Return the wheel-build source for the current worker: always a per-worker
    ``git clone --shared``, so no ``pip install <src>`` ever builds in the
    checkout — and so this tier only ever sees COMMITTED content.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "serial")
    cached = _CACHE.get(worker)
    if cached is not None:
        return cached
    dst = Path(tempfile.mkdtemp(prefix=f"hats-buildsrc-{worker}-"))
    src = dst / "repo"
    # No explicit env on purpose: the autouse `_isolate_git_env` fixture already strips
    # GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE for every test (HATS-886), and passing an
    # os.environ-derived env here re-leaks the class that guard exists to prevent.
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(repo_root), str(src)],
        check=True,
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT_S,
    )
    _CACHE[worker] = src
    return src
