"""Per-xdist-worker private repo source for in-tree wheel builds (HATS-589).

PITFALL 1 (HATS-589 / HATS-568): ``pip install <local-repo>`` builds the
wheel **in-tree** — pip 21.3+ defaults to in-tree builds and there is no
flag to revert it. The wheel lands in ``<repo>/build/bdist.*`` +
``<repo>/build/lib``, whose paths are version/platform-derived, NOT
process-unique. Under ``pytest -n>1`` every worker that runs
``ai-hats self update`` (or the shared-venv build) against the single
shared checkout writes into the same ``<repo>/build/`` concurrently →
the ``[Errno 17] File exists: build/bdist...dist-info`` collision class.

:func:`build_src` resolves the install source so each xdist worker builds
in its OWN private clone:

* **Serial run** (no ``PYTEST_XDIST_WORKER``) → returns ``repo_root``
  unchanged. A single process building in-tree never races itself, and
  the session-autouse ``_clean_repo_build_dir`` rmtree keeps it clean.
* **Under xdist** → a once-per-worker ``git clone --shared`` of
  ``repo_root`` into a tmp dir. ``--shared`` references the source object
  store (no object copy → fast); only the working tree is materialised.
  Each worker's clone owns its own ``build/`` → no cross-worker race.
  The wheel build needs only the materialised working tree, so the
  borrowed object store matters solely *during* the clone — the guarantee
  assumes no ``git gc`` on the source repo within that narrow window
  (always true for a worktree under an active test session).

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


def build_src(repo_root: Path) -> Path:
    """Return the wheel-build source for the current worker.

    Serial → ``repo_root``. Under xdist → a per-worker ``git clone --shared``
    so concurrent ``pip install <src>`` builds never race ``<repo>/build/``.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return repo_root
    cached = _CACHE.get("src")
    if cached is not None:
        return cached
    dst = Path(tempfile.mkdtemp(prefix=f"hats-buildsrc-{worker}-"))
    src = dst / "repo"
    subprocess.run(
        ["git", "clone", "--shared", "--quiet", str(repo_root), str(src)],
        check=True, capture_output=True, text=True,
    )
    _CACHE["src"] = src
    return src
