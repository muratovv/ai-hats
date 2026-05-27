"""E2E smoke: ``ai-hats self update`` still installs cleanly without ``--no-cache-dir`` (HATS-563).

The bug it catches:

  Before HATS-563 ``cli/maintenance.py:_build_update_cmd`` passed
  ``--no-cache-dir`` alongside ``--force-reinstall``. ``--force-reinstall``
  already re-installs the named target unconditionally, so the extra
  ``--no-cache-dir`` only defeated pip's local wheel/http cache. Cold-
  cache CI was unaffected but every developer-machine ``self update``
  re-downloaded ~50 MB of PyPI wheels for no reason. On the HATS-550
  e2e+smoke pre-push gate this cost ~100s per full run.

Test scope (deliberate):

  This is the **e2e smoke gate** for the change — it asserts the real
  ``ai-hats`` binary still completes a ``self update --revision`` flow
  end-to-end after ``--no-cache-dir`` was removed, with a real launcher
  + real pip + real venv (per ``dev_rule_e2e_gate``).

  The **precise fail-under-revert canary** is the unit test in
  ``tests/test_cli_init_flow.py::test_update_command_uses_force_reinstall``
  which asserts ``--no-cache-dir`` is absent from ``_build_update_cmd``.
  That assertion flips immediately when the flag is restored.

Why not a timing / filesystem / log-grep fail-under-revert here:

  - ``cli/maintenance.py`` invokes pip with ``capture_output=True``,
    so pip's stdout is unreachable through the outer subprocess.
  - Pip 26 prints ``Downloading <wheel>`` for both cache hits and PyPI
    fetches under ``--force-reinstall``, so PIP_LOG grep is unreliable.
  - The build environment pip subprocess (installing setuptools /
    setuptools_scm / vcs_versioning to build the ai-hats wheel) writes
    to ``PIP_CACHE_DIR/http-v2/`` regardless of the outer pip's
    ``--no-cache-dir`` flag, masking any filesystem differential.
  - Timing differentials between warm and cold cache on this specific
    ``--revision <SHA> --force`` code path are ~3s on 30s (within CI
    variance), too noisy for a robust threshold.

  The unit test owns the precise behavioural contract; this test owns
  the real-binary smoke coverage.

Setup contract (real subprocess + real pip):

  - ``src-repo``     — clone of REPO_ROOT (carries tags + history so
                       the ``--revision`` install path is reachable).
  - ``pip-cache``    — dedicated tmp dir exported as ``PIP_CACHE_DIR``
                       (avoids polluting the host pip cache).
  - launcher install — bootstraps the launcher binary + first venv via
                       ``scripts/install-launcher.sh`` and the first
                       ``self update`` (real pip install).
  - ``self update --revision <SHA> --force`` — runs ``_build_update_cmd``
                       through the real ai-hats binary at the same SHA
                       so the post-call binary still recognises every
                       flag (using a release tag would downgrade to a
                       binary that lacks ``--revision``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    """Run a subprocess; assert exit code matches ``expect_exit``."""
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_self_update_completes_without_no_cache_dir(tmp_path: Path) -> None:
    """End-to-end smoke: the real binary survives ``self update`` after the patch.

    Two assertions amortize the heavy setup (~80s: clone + launcher
    install + bootstrap pip + --force-reinstall at a real SHA):

    1. ``self update --revision <SHA> --force`` exits 0.
    2. The post-install binary still runs (``ai-hats --version`` exits
       0 with a non-empty version string), proving the wheel produced
       by the patched pip command is usable.
    """
    src_repo = tmp_path / "src-repo"
    pip_cache = tmp_path / "pip-cache"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pip_cache.mkdir()

    # ----- fixture: src-repo (clone of REPO_ROOT, carries history) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )

    # Pin to src-repo's current HEAD SHA. Pinning to a release tag (e.g.
    # ``v0.7.0``) would downgrade ai-hats to a binary lacking newer
    # flags (``--revision`` itself was added in HATS-496) and break
    # follow-up invocations.
    sha_probe = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    pinned_ref = sha_probe.stdout.strip()

    # ----- bootstrap env: dedicated pip cache, isolated from host -----
    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env["PIP_CACHE_DIR"] = str(pip_cache)
    env.pop("AI_HATS_VENV", None)
    env.pop("PYTHONPATH", None)
    # PIP_NO_CACHE_DIR from the host shell would override PIP_CACHE_DIR.
    env.pop("PIP_NO_CACHE_DIR", None)

    # ----- bootstrap: install launcher + first self update -----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=240)

    # Switch to git+file:// so --revision is reachable. Same rationale
    # as test_self_update_revision.py:150.
    env["AI_HATS_REPO_URL"] = f"git+file://{src_repo}"

    # ----- assertion 1: production code path exits 0 -----
    # If removing --no-cache-dir broke the pip install dance somehow
    # (e.g. cache corruption, deprecated flag interactions), this would
    # exit non-zero and the _run helper would assert.
    _run(
        [str(launcher_dest), "self", "update",
         "--revision", pinned_ref, "--force"],
        cwd=project, env=env, timeout=300,
    )

    # ----- assertion 2: the installed binary still runs -----
    # Validates the wheel built by the patched pip command is usable;
    # any binary-level breakage (missing transitive dep, broken
    # entrypoint) would surface here rather than at the user's next
    # CLI invocation.
    version_result = _run(
        [str(launcher_dest), "--version"],
        cwd=project, env=env, timeout=30,
    )
    assert version_result.stdout.strip(), (
        f"ai-hats --version printed empty stdout after self update:\n"
        f"stderr:\n{version_result.stderr}"
    )
