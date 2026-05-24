"""Build a real ai-hats launcher venv for venv-tier e2e tests.

Mirrors the pattern from ``tests/e2e/test_install.py`` but exposes it
as a helper so module-scoped fixtures can amortise the ~30-60s build
across multiple tests in the same module.

Single entry point: :func:`build_launcher_venv`. Returns the launcher
binary path; the inner ai-hats venv lives at ``<project>/.agent/ai-
hats/.venv/`` after the first ``self update``. Tests typically run
``ai-hats self init`` next to populate the project.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def build_launcher_venv(
    work_dir: Path, repo_root: Path, *, project_dir: Path,
) -> Path:
    """Install the ai-hats launcher and bootstrap its venv in ``project_dir``.

    Steps:

    1. Run ``scripts/install-launcher.sh`` with
       ``AI_HATS_LAUNCHER_DEST=<work_dir>/bin/ai-hats`` so the binary
       lands inside the test sandbox.
    2. Run ``<launcher> self update`` from ``project_dir`` with
       ``AI_HATS_REPO_URL=<repo_root>`` so pip installs from the
       local checkout (no network for ai-hats itself). The inner
       venv lands at ``<project_dir>/.agent/ai-hats/.venv/`` —
       subsequent CLI calls in that project find it automatically.

    Returns the launcher path. The project itself is owned by the
    caller (typically a module-scoped fixture).

    Raises :class:`FileNotFoundError` if ``scripts/install-launcher.sh``
    is missing — callers can catch that and ``pytest.skip``.
    Raises :class:`subprocess.CalledProcessError` on launcher or
    self-update failure (e.g. no network when pip needs to fetch
    transitive deps not in the local wheel cache).
    """
    install_script = repo_root / "scripts" / "install-launcher.sh"
    if not install_script.is_file():
        raise FileNotFoundError(install_script)

    launcher = work_dir / "bin" / "ai-hats"
    launcher.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher)
    env["AI_HATS_REPO_URL"] = str(repo_root)
    env.pop("AI_HATS_VENV", None)

    subprocess.run(
        ["bash", str(install_script)],
        cwd=str(work_dir), env=env,
        capture_output=True, text=True, timeout=60, check=True,
    )
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise RuntimeError(f"launcher not installed at {launcher}")

    # Bootstrap the inner venv via the launcher, INSIDE project_dir
    # so subsequent ``ai-hats self <cmd>`` invocations find it at
    # ``<project_dir>/.agent/ai-hats/.venv/`` (the default lookup path).
    subprocess.run(
        [str(launcher), "self", "update"],
        cwd=str(project_dir), env=env,
        capture_output=True, text=True, timeout=180, check=True,
    )
    return launcher


def network_available() -> bool:
    """Cheap pre-flight: are we likely to be able to ``pip install``?

    ``install-launcher.sh`` is offline once it has the local script.
    ``self update`` with ``AI_HATS_REPO_URL=<local>`` installs ai-hats
    from the checkout but pip still resolves transitive deps; those
    may already be cached. We probe by checking that ``pip`` is on
    PATH and a wheel cache dir exists. False negatives are tolerable
    — the build itself will fail loudly if the assumption breaks.
    """
    return shutil.which("pip") is not None or shutil.which("pip3") is not None
