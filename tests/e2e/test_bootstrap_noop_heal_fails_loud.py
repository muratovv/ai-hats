"""E2E: bootstrap_or_die() fails loud on a no-op heal instead of looping forever (HATS-1359).

Value under test: `uv pip install <dist>` can exit 0 without fixing anything —
a bare/unconstrained requirement is satisfied by a dist-info whose importable
files are gone (the ``~/dotfiles`` incident: HATS-1262 deleted
``packages/ai-hats-tracker``, but that project's editable ``ai-hats`` metadata
predated the deletion, so its ``.pth`` kept pointing at the now-gone source
dir). Before the fix, ``bootstrap_or_die()`` trusted that exit code and
``os.execv``'d unconditionally, reproducing the identical missing dep in the
fresh interpreter forever — a real hang, escapable only via Ctrl-C.

Setup (real launcher build + real `uv`, per ``dev_rule_e2e_gate`` — no
stubs): build a real launcher venv via
:func:`tests.e2e._helpers.venv.build_launcher_venv`, then reproduce the no-op
heal generically (not tracker-specific): delete ``ptyprocess`` — a real,
already-installed runtime dep — leaving its ``dist-info`` in place. Confirmed
empirically (see task HATS-1359 plan) that ``uv pip install ptyprocess``
against that state audits as already-satisfied and no-ops (exit 0) without
touching the network.

Assertion: `python -m ai_hats config status` exits 1 quickly (bounded by a
subprocess timeout — the bug this guards against hangs, it doesn't merely
slow down) with the still-missing dep and the rescue command on stderr.
``--version``/``--help`` are click eager options that short-circuit before
``cli.main()``'s body (and therefore ``bootstrap_or_die()``) ever runs, so a
real subcommand is required to actually exercise the gate.

Fail-under-revert: drop the ``still_missing`` recheck in
``bootstrap_or_die()`` (restore the old unconditional ``os.execv``) and this
test times out (``subprocess.TimeoutExpired``) instead of observing a clean
``exit(1)``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.install_heavy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _site_packages(venv: Path) -> Path:
    cands = sorted((venv / "lib").glob("python*/site-packages"))
    assert cands, f"no site-packages found under {venv}"
    return cands[0]


@pytest.mark.integration
def test_bootstrap_or_die_fails_loud_on_noop_heal(tmp_path: Path) -> None:
    """A dep whose dist-info survives but whose module dir is gone → clean exit(1), no hang."""
    from _helpers.venv import build_launcher_venv, network_available, venv_unavailable

    if not network_available():
        venv_unavailable("uv not on PATH — cannot build launcher venv")

    work = tmp_path / "wt"
    work.mkdir()
    try:
        _launcher, venv = build_launcher_venv(work, REPO_ROOT)
    except FileNotFoundError as exc:
        venv_unavailable(f"install-launcher.sh missing: {exc}")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        venv_unavailable(f"launcher venv build failed/timed out: {exc}")

    venv_python = venv / "bin" / "python"
    pty_pkg = _site_packages(venv) / "ptyprocess"
    assert pty_pkg.is_dir(), f"ptyprocess not found at {pty_pkg}"
    shutil.rmtree(pty_pkg)  # dist-info (RECORD) survives — only the module is gone

    broken = subprocess.run(
        [str(venv_python), "-c", "import ptyprocess"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert broken.returncode != 0, "ptyprocess still importable after deletion — setup failed"

    env = os.environ.copy()
    env["UV_OFFLINE"] = "1"  # a real fetch would mask the no-op-heal premise

    # A real subcommand, not --version/--help (click eager options that
    # short-circuit before cli.main()'s body — and bootstrap_or_die() with it
    # — ever runs).
    result = subprocess.run(
        [str(venv_python), "-m", "ai_hats", "config", "status"],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,  # the regression this guards against hangs, it isn't merely slow
    )
    assert result.returncode == 1, (
        f"expected a clean exit(1), got {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    assert "ptyprocess" in result.stderr
    assert "uv pip install" in result.stderr
