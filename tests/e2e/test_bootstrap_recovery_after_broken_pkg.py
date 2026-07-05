"""E2E: ``bootstrap.sh --repair`` re-establishes a broken managed install (HATS-791).

Value under test: bootstrap.sh is the canonical OUT-OF-BAND recovery hatch.
When the managed venv is broken badly enough that the in-band ``ai-hats self
update`` can't fix itself (it runs FROM that venv), a fetched-fresh
``bootstrap.sh --repair`` rebuilds it by driving the launcher via its ABSOLUTE
path (``"$LAUNCHER_DEST" self update``) — paradox-immune.

Setup (real launcher build + real ``self update`` rebuild, per
``dev_rule_e2e_gate`` — no stubs):

  - Build a real launcher venv via
    :func:`tests.e2e._helpers.venv.build_launcher_venv` (launcher +
    ``<bootstrap>/.agent/ai-hats/.venv``, installed from the local repo).
  - Corrupt the install: delete ``<venv>/.../site-packages/ai_hats`` so
    ``python -c "import ai_hats"`` fails (the venv interpreter survives but the
    package is gone — exactly the un-self-healable state).
  - Run ``bootstrap.sh --repair`` from the bootstrap dir, pointing the launcher
    install at a ``file://`` URL (``AI_HATS_INSTALL_LAUNCHER_URL`` /
    ``AI_HATS_LAUNCHER_URL``) and the ai-hats source at the local repo
    (``AI_HATS_REPO_URL``) — no network.

Assertion: after ``--repair`` the launcher exists and
``<venv>/bin/python -m ai_hats --version`` runs (import works again).

Fail-under-revert: break the ``"$LAUNCHER_DEST"`` absolute-path call in
bootstrap.sh (e.g. invoke a bare ``ai-hats``) and the rebuild never runs from
the freshly-installed launcher → the post-repair import assertion fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real launcher build + self update at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
LAUNCHER_SRC = REPO_ROOT / "scripts" / "ai-hats-launcher"
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _all_managed_venvs(ah_dir: Path) -> list[Path]:
    """Every managed venv root under ``.agent/ai-hats/`` (default + versioned).

    A managed ``self update`` may install into ``versions/<sha>/`` (HATS-647)
    rather than the legacy ``.venv``, so the *active* venv the launcher resolves
    isn't necessarily ``.venv``. We collect both shapes and corrupt every one so
    the import is guaranteed broken regardless of which the launcher picks.
    """
    roots: list[Path] = []
    if (ah_dir / ".venv" / "bin" / "python").exists():
        roots.append(ah_dir / ".venv")
    versions = ah_dir / "versions"
    if versions.is_dir():
        roots += [d for d in versions.iterdir() if (d / "bin" / "python").exists()]
    return roots


def _site_packages_ai_hats(venv: Path) -> Path | None:
    """Locate ``site-packages/ai_hats`` inside ``venv`` (lib/python*/...)."""
    for cand in sorted((venv / "lib").glob("python*/site-packages/ai_hats")):
        if cand.is_dir():
            return cand
    return None


@pytest.mark.integration
def test_bootstrap_repair_rebuilds_broken_venv(tmp_path: Path) -> None:
    """A venv with its ai_hats package deleted is rebuilt by `bootstrap.sh --repair`."""
    from _helpers.venv import (
        build_launcher_venv,
        network_available,
        venv_unavailable,
    )

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build launcher venv")

    work = tmp_path / "wt"
    work.mkdir()
    try:
        launcher, venv = build_launcher_venv(work, REPO_ROOT)
    except FileNotFoundError as exc:
        venv_unavailable(f"install-launcher.sh missing: {exc}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        venv_unavailable(f"launcher venv build failed/timed out: {exc}")

    bootstrap_dir = work / "bootstrap"
    ah_dir = bootstrap_dir / ".agent" / "ai-hats"
    venv_python = venv / "bin" / "python"

    # --- sanity: the freshly-built venv imports ai_hats ---
    pre = subprocess.run(
        [str(venv_python), "-c", "import ai_hats"],
        capture_output=True, text=True, timeout=60,
    )
    assert pre.returncode == 0, f"venv broken before corruption:\n{pre.stderr}"

    # --- corrupt: delete the installed package in EVERY managed venv (default
    #     .venv + any versions/<sha>/) so the active install is unimportable
    #     whichever the launcher resolves. Interpreter survives — exactly the
    #     un-self-healable state. ---
    import shutil

    managed = _all_managed_venvs(ah_dir)
    assert managed, f"no managed venv found under {ah_dir}"
    corrupted = 0
    for root in managed:
        pkg = _site_packages_ai_hats(root)
        if pkg is not None:
            shutil.rmtree(pkg)
            corrupted += 1
    assert corrupted, "no site-packages/ai_hats located to corrupt"
    broken = subprocess.run(
        [str(venv_python), "-c", "import ai_hats"],
        capture_output=True, text=True, timeout=60,
    )
    assert broken.returncode != 0, "package still importable after deletion — corruption failed"

    # --- repair out-of-band: fetched-fresh bootstrap, file:// launcher URL,
    #     local repo source. No network. ---
    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env["AI_HATS_INSTALL_LAUNCHER_URL"] = f"file://{INSTALL_LAUNCHER}"
    env["AI_HATS_LAUNCHER_URL"] = f"file://{LAUNCHER_SRC}"
    from _helpers.repo_src import build_src

    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env.pop(ENV_AI_HATS_VENV, None)  # let repair target the managed default venv
    env.pop("PYTHONPATH", None)

    repair = subprocess.run(
        ["bash", str(BOOTSTRAP), "--repair"],
        cwd=str(bootstrap_dir), env=env, capture_output=True, text=True, timeout=600,
    )
    combined = repair.stdout + repair.stderr
    assert repair.returncode == 0, f"`bootstrap.sh --repair` failed:\n{combined}"

    # --- assert a working launcher + venv is re-established ---
    assert launcher.is_file() and os.access(launcher, os.X_OK), "launcher missing after repair"
    # Drive the REAL launcher (absolute path) from the bootstrap project: it
    # resolves the rebuilt venv and execs `python -m ai_hats`. A bare ai-hats is
    # NOT used, so this also exercises the absolute-path recovery contract.
    post = subprocess.run(
        [str(launcher), "--version"],
        cwd=str(bootstrap_dir), env=env, capture_output=True, text=True, timeout=120,
    )
    assert post.returncode == 0, (
        f"launcher must run after repair (venv rebuilt):\n{post.stdout}\n{post.stderr}"
    )
