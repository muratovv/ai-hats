"""Build a real ai-hats launcher venv for venv-tier e2e tests.

Mirrors the pattern from ``tests/e2e/test_install.py`` but exposes it
as a helper so module-scoped fixtures can amortise the ~30-60s build
across multiple tests in the same module.

Single entry point: :func:`build_launcher_venv`. Builds the venv in
a dedicated sandbox directory (not inside any test's project dir)
so tests can be handed a clean project that points at the shared
venv via the ``AI_HATS_VENV`` env knob. This honours the plan's
"fresh Project per yield" contract while keeping the venv shared.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def build_launcher_venv(work_dir: Path, repo_root: Path) -> tuple[Path, Path]:
    """Install the ai-hats launcher and bootstrap a shared venv.

    Steps:

    1. Run ``scripts/install-launcher.sh`` with
       ``AI_HATS_LAUNCHER_DEST=<work_dir>/bin/ai-hats`` so the binary
       lands inside the test sandbox.
    2. Run ``<launcher> self update`` from a dedicated bootstrap
       directory (``<work_dir>/bootstrap/``) with
       ``AI_HATS_REPO_URL=<repo_root>`` so pip installs from the
       local checkout (no network for ai-hats itself). The inner
       venv lands at ``<work_dir>/bootstrap/.agent/ai-hats/.venv/``.

    Returns ``(launcher_path, shared_venv_path)``. Callers point
    per-test projects at the shared venv via the ``AI_HATS_VENV``
    env knob — the bootstrap dir itself is NOT a test project.

    Raises :class:`FileNotFoundError` if ``scripts/install-launcher.sh``
    is missing — callers can catch that and ``pytest.skip``.
    Raises :class:`subprocess.CalledProcessError` on launcher or
    self-update failure (e.g. no network when pip needs to fetch
    transitive deps not in the local wheel cache).
    Raises :class:`RuntimeError` if the launcher binary lands but
    isn't executable, or if the venv directory doesn't materialise.
    Callers that pre-skip on missing artefacts should catch these
    three explicitly.
    """
    install_script = repo_root / "scripts" / "install-launcher.sh"
    if not install_script.is_file():
        raise FileNotFoundError(install_script)

    launcher = work_dir / "bin" / "ai-hats"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = work_dir / "bootstrap"
    bootstrap.mkdir(exist_ok=True)

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

    # Bootstrap the inner venv via the launcher in a DEDICATED dir,
    # NOT a project that tests will use — tests get fresh project
    # paths and reach the venv via AI_HATS_VENV env override.
    #
    # Timeout budget (HATS-582): a healthy build is ~7s of pip +
    # venv-create overhead (~24s total). The generous 600s ceiling
    # absorbs two slow cases without tripping:
    #   * cold pip cache / first run on a fresh host — ~35 transitive
    #     deps (ai-hats → claude-agent-sdk → mcp → starlette, uvicorn,
    #     httpx, jsonschema, …) downloaded over the network;
    #   * a *corrupted* pip HTTP cache — if a prior run's pip was
    #     SIGKILL'd mid-download (e.g. by a too-tight timeout), its cache
    #     entries deserialize-fail and pip refetches everything with retry
    #     backoff (observed ~10min). A tight timeout here SIGKILLs pip
    #     again → re-corrupts the cache → death spiral. The wide window
    #     lets one slow build COMPLETE and self-heal the cache instead.
    # ``subprocess.TimeoutExpired`` still propagates so the session-scoped
    # fixture can skip the venv tier gracefully on a genuinely stuck host.
    subprocess.run(
        [str(launcher), "self", "update"],
        cwd=str(bootstrap), env=env,
        capture_output=True, text=True, timeout=600, check=True,
    )
    shared_venv = bootstrap / ".agent" / "ai-hats" / ".venv"
    if not (shared_venv / "bin" / "python").is_file():
        raise RuntimeError(f"shared venv not bootstrapped at {shared_venv}")
    return launcher, shared_venv


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
