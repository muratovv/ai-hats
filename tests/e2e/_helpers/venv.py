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
from typing import NoReturn

import pytest


# HATS-645: the master pre-push e2e gate sets this to "1" so a venv-tier that
# cannot build fails-closed instead of skipping (see :func:`venv_unavailable`).
REQUIRE_VENV_ENV = "AI_HATS_E2E_REQUIRE_VENV"


def build_launcher_venv(work_dir: Path, repo_root: Path) -> tuple[Path, Path]:
    """Install the ai-hats launcher and bootstrap a shared venv.

    Steps:

    1. Run ``scripts/install-launcher.sh`` with
       ``AI_HATS_LAUNCHER_DEST=<work_dir>/bin/ai-hats`` so the binary
       lands inside the test sandbox.
    2. Pin the bootstrap dir to the edge channel (HATS-780, via
       :func:`tests.e2e._helpers.project.pin_edge_channel`) so the next
       step resolves the local ``AI_HATS_REPO_URL`` source, NOT PyPI.
       Without it, post-764 a missing harness block defaults to ``stable``
       → the PyPI JSON API (ai-hats unpublished pre-765 → 404, fail-loud) →
       fail-closed under ``AI_HATS_E2E_REQUIRE_VENV=1``.
    3. Run ``<launcher> self update`` from that bootstrap directory
       (``<work_dir>/bootstrap/``) with ``AI_HATS_REPO_URL=<repo_root>``
       so pip installs from the local checkout (no network for ai-hats
       itself). The inner venv lands at
       ``<work_dir>/bootstrap/.agent/ai-hats/.venv/``.

    Returns ``(launcher_path, shared_venv_path)``. Callers point
    per-test projects at the shared venv via the ``AI_HATS_VENV``
    env knob — the bootstrap dir itself is NOT a test project.

    Raises :class:`FileNotFoundError` if ``scripts/install-launcher.sh``
    is missing — callers can catch that and ``pytest.skip``.
    Raises :class:`subprocess.CalledProcessError` on launcher or
    self-update failure (e.g. no network when pip needs to fetch
    transitive deps not in the local wheel cache). The edge pin (step 2)
    removes the post-764 stable→PyPI-404 failure mode for the local source.
    Raises :class:`RuntimeError` if the launcher binary lands but
    isn't executable, or if the venv directory doesn't materialise.
    Callers that pre-skip on missing artefacts should catch these
    three explicitly.

    Deliberate long helper API contract — noqa: comment-length.
    """
    install_script = repo_root / "scripts" / "install-launcher.sh"
    if not install_script.is_file():
        raise FileNotFoundError(install_script)

    launcher = work_dir / "bin" / "ai-hats"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = work_dir / "bootstrap"
    bootstrap.mkdir(exist_ok=True)

    # HATS-589: build from a per-xdist-worker private clone so concurrent
    # workers don't race the shared <repo>/build/ wheel dir. No-op (returns
    # repo_root) on a serial run.
    from _helpers.repo_src import build_src

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher)
    env["AI_HATS_REPO_URL"] = str(build_src(repo_root))
    env.pop("AI_HATS_VENV", None)

    subprocess.run(
        ["bash", str(install_script)],
        cwd=str(work_dir), env=env,
        capture_output=True, text=True, timeout=60, check=True,
    )
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise RuntimeError(f"launcher not installed at {launcher}")

    # HATS-780: pin the bootstrap to the edge channel BEFORE `self update`.
    # Post-764 a missing harness block defaults to `stable`, which resolves the
    # target from PyPI (ai-hats unpublished pre-765 → 404 → fail-closed under
    # AI_HATS_E2E_REQUIRE_VENV=1, ERROR-ing every shared-venv test). Edge
    # resolves the local `AI_HATS_REPO_URL` set above (git ls-remote on the
    # local path — no network, no PyPI). Mirrors the `pin_edge_channel` every
    # other e2e `self update` already applies; this session-shared bootstrap is
    # the one HATS-764 missed.
    from _helpers.project import pin_edge_channel

    pin_edge_channel(bootstrap)

    # Bootstrap the inner venv via the launcher in a DEDICATED dir,
    # NOT a project that tests will use — tests get fresh project
    # paths and reach the venv via AI_HATS_VENV env override.
    #
    # Timeout budget (HATS-582 / HATS-763): a healthy build is fast under uv
    # (~2s warm cache). The generous 600s ceiling absorbs the slow case without
    # tripping: a cold uv cache / first run on a fresh host downloads ~35
    # transitive deps (ai-hats → claude-agent-sdk → mcp → starlette, uvicorn,
    # httpx, jsonschema, …) over the network, and `uv venv --python 3.11` may
    # provision the interpreter itself. ``subprocess.TimeoutExpired`` still
    # propagates so the session-scoped fixture can skip the venv tier gracefully
    # on a genuinely stuck host.
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
    """Cheap pre-flight: is the uv engine present so a build can even start?

    ``install-launcher.sh`` is offline once it has the local script.
    ``self update`` with ``AI_HATS_REPO_URL=<local>`` installs ai-hats from the
    checkout but uv still resolves transitive deps; those may already be in the
    uv cache. We probe only that ``uv`` is on PATH (HATS-763 — the single host
    prerequisite). Note: uv-present ≠ network — a False here means "no engine",
    not "offline"; the build itself fails loudly if the network assumption breaks.
    """
    return shutil.which("uv") is not None


def venv_unavailable(reason: str) -> NoReturn:
    """Fail-or-skip when the shared venv tier cannot build (HATS-645).

    Normal (local) runs ``pytest.skip`` — an offline dev still gets a green
    suite minus the venv tier, the "degrade, not cascade" contract that keeps a
    stuck build from cascading into ERRORs across ~17 dependent files.

    But when the master pre-push gate exports ``AI_HATS_E2E_REQUIRE_VENV=1``, a
    venv build that cannot happen is a **failure**, not a skip. A silent skip
    there is the false-green that let master ship with two real e2e failures
    (HATS-645 Problem 2): the gate runs ``pytest -m "integration or smoke"`` and
    treats a 0 exit as "suite passed", so skipped tier-2 tests pass the gate
    even though they would FAIL if actually run. Fail-closed: cannot verify ⇒
    cannot push (the gate's stated contract — ``--no-verify`` is the only
    escape).
    """
    if os.environ.get(REQUIRE_VENV_ENV) == "1":
        pytest.fail(
            f"venv-tier required ({REQUIRE_VENV_ENV}=1) but unavailable — "
            f"fail-closed (HATS-645): {reason}",
            pytrace=False,
        )
    pytest.skip(reason)
