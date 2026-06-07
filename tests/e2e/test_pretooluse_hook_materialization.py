"""E2E (HATS-467): PreToolUse hook scripts materialized to disk.

Four contracts a reviewer can refute by reverting the relevant code:

1. ``ai-hats self init`` writes ``<ai_hats_dir>/library/hooks/*.sh``
   with mode ``0o755``, matching the package-data source bytes, plus
   a ``.manifest`` listing them.
2. A second ``self init`` is idempotent — bytes-identical files do
   not get rewritten (no spurious mtime updates).
3. After mutating a materialized hook by hand and re-running
   ``self update``, the file is restored to package-data bytes
   (refresh actually fires through ``Assembler.bump``).
4. The materialized hook is functional: piping a classifier-matching
   ``tool_input`` JSON to it (without a TTY, no
   ``AI_HATS_SHARED_STATE_ACK``) yields exit 2 — proof that the
   HATS-437 safety net is alive after this task lands.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"
HOOK_BASENAMES = ("pre_bash_shared_state_guard.sh", "shared_state_classifier.sh")

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.repo_src import build_src  # noqa: E402


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.fixture
def installed_launcher(shared_launcher, tmp_path_factory):
    """Read-only tests (A/B/D) on the session-scoped shared venv (HATS-582).

    Tests A (init materializes), B (idempotent re-init) and D (safety net
    live) only ``self init`` into a fresh ``tmp_path`` project and read the
    materialized hooks back — they never mutate the venv, so they reuse the
    single session venv from :func:`tests.e2e.conftest.shared_launcher`.

    The shared ``env`` is NEUTRAL; this module needs two extra hygiene knobs
    that the old module fixture applied, so we layer them on a COPY:

    * pop ``PYTHONPATH`` — ``ai-hats wt exec`` sets ``PYTHONPATH=src`` which
      shadows the installed ``ai_hats`` package with the source tree (which
      lacks the ``library`` subpackage) → "no roles found".
    * isolate ``HOME`` to an empty tmpdir — otherwise the dev user's
      ``~/.ai-hats/`` customizations (personal-workflow trait, custom roles)
      bleed into composition and shadow framework roles.

    Returns ``(launcher, env, shared_venv)`` — the same shape Test C's
    :func:`private_launcher` returns.

    Test C (``test_e2e_self_update_refreshes_hook_after_drift``) runs
    ``self update --force-downgrade`` which REINSTALLS into the pinned venv —
    it is the lone mutator and keeps a private builder (:func:`private_launcher`).
    """
    launcher, base_env, shared_venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    isolated_home = tmp_path_factory.mktemp("pretooluse-home")
    env["HOME"] = str(isolated_home)
    return launcher, env, shared_venv


@pytest.fixture(scope="module")
def private_launcher(tmp_path_factory):
    """Private module-scoped builder for the LONE venv-mutating test (HATS-582).

    Test C runs ``self update --force-downgrade``, which reinstalls ai-hats
    into the pinned venv — a destructive mutation that would poison the
    session-shared venv. So this test keeps its own private build (the old
    module fixture, unchanged).
    """
    tmp = tmp_path_factory.mktemp("launcher")
    launcher_dest = tmp / "bin" / "ai-hats"
    launcher_dest.parent.mkdir(parents=True)

    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(build_src(REPO_ROOT))
    env.pop("AI_HATS_VENV", None)
    # PYTHONPATH=src (set by ``ai-hats wt exec``) would shadow the
    # installed ``ai_hats`` package inside the test venv, and the
    # source ``src/ai_hats`` directory does NOT carry the ``library``
    # subpackage (mapped to ``<repo>/library`` only at install time
    # via ``pyproject.toml`` ``package-dir``). Importing the source
    # copy makes ``importlib.resources.files('ai_hats.library')``
    # silently resolve to an empty layout → "no roles found".
    env.pop("PYTHONPATH", None)
    # Isolate from the user's ``~/.ai-hats/`` customizations layer
    # (roles, traits, customizations.yaml) — otherwise the dev env's
    # personal-workflow trait and custom roles bleed into the test
    # and shadow framework roles. HOME redirected to an empty tmpdir.
    isolated_home = tmp / "home"
    isolated_home.mkdir()
    env["HOME"] = str(isolated_home)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp, env=env, timeout=30)
    bootstrap_proj = tmp / "_bootstrap_proj"
    bootstrap_proj.mkdir()
    # --force-downgrade: required when this dev env is ahead of
    # origin/master (HATS-441 guard would otherwise refuse the install
    # of the local repo path). Safe in test: we own the bootstrap_proj
    # entirely. Same workaround used by long-running e2e fixtures.
    # HATS-673: timeout 300 (not 180) — this is a REAL pip install of
    # ai-hats into a fresh venv (~118s solo). Under the master gate's
    # `-n8 --dist=loadgroup` run, concurrent pip installs across workers
    # push a single build past 180s and FLAKE the gate. 300s is the
    # suite-proven ceiling: ~10 sibling test_self_update_* tests do the
    # same real-pip `self update` under the same -n8 contention at 300s
    # and don't flake (crash_safety / orphan_gc / versioned). This call
    # was the lone <300 outlier on the pip path.
    _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=bootstrap_proj, env=env, timeout=300,
    )
    shared_venv = bootstrap_proj / ".agent" / "ai-hats" / ".venv"
    assert shared_venv.is_dir(), "bootstrap did not create shared venv"
    env["AI_HATS_VENV"] = str(shared_venv)
    return launcher_dest, env, shared_venv


def _init_minimal_project(launcher: Path, env: dict, project: Path) -> None:
    """Wire ai-hats into ``project`` with assistant role + Claude provider."""
    project.mkdir(exist_ok=True)
    _run(
        [str(launcher), "self", "init", "-p", "claude",
         "-r", "assistant", "--no-wizard"],
        cwd=project, env=env, timeout=120,
    )


def _materialized_hooks_dir(project: Path) -> Path:
    return project / ".agent" / "ai-hats" / "library" / "hooks"


def _package_hook_source_bytes(name: str) -> bytes:
    """Read the source hook body straight from ``REPO_ROOT/library/hooks/``.

    pyproject.toml maps ``ai_hats.library`` package-dir to ``<repo>/library/``,
    so the on-disk source under ``library/hooks/`` IS the package data the
    materialize step copies from. Avoids depending on the test venv's
    Python-side import machinery to read those bytes back.
    """
    src = REPO_ROOT / "library" / "hooks" / name
    return src.read_bytes()


# ---------------------- Test A: init materializes ----------------------


@pytest.mark.integration
def test_e2e_init_materializes_hooks_executable(
    installed_launcher, tmp_path
):
    """`self init` writes both hooks +x with package-data bytes + manifest."""
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_init_materialize"
    _init_minimal_project(launcher, env, project)

    hooks_dir = _materialized_hooks_dir(project)
    assert hooks_dir.is_dir(), (
        f"hooks dir missing: {hooks_dir}; init must mkdir + populate"
    )

    for name in HOOK_BASENAMES:
        f = hooks_dir / name
        assert f.is_file(), f"materialized hook missing: {f}"
        # 0o755 — executable by all, writable by owner only.
        mode = stat.S_IMODE(f.stat().st_mode)
        assert mode == 0o755, (
            f"{name} mode is {oct(mode)}, expected 0o755 — "
            "safe_delete.replace(mode=...) regression"
        )
        # Bytes identical to package data — guarantees we copied from
        # the right source.
        assert f.read_bytes() == _package_hook_source_bytes(name), (
            f"materialized {name} diverges from package source"
        )

    manifest = hooks_dir / ".manifest"
    assert manifest.is_file(), f".manifest missing under {hooks_dir}"
    manifest_text = manifest.read_text()
    for name in HOOK_BASENAMES:
        assert name in manifest_text, (
            f"{name} absent from manifest:\n{manifest_text}"
        )


# ---------------------- Test B: idempotent re-init ----------------------


@pytest.mark.integration
def test_e2e_init_materialize_is_idempotent(installed_launcher, tmp_path):
    """Second ``self init`` does not rewrite identical files (mtime stable)."""
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_init_idempotent"
    _init_minimal_project(launcher, env, project)

    hooks_dir = _materialized_hooks_dir(project)
    guard = hooks_dir / "pre_bash_shared_state_guard.sh"
    first_mtime = guard.stat().st_mtime_ns

    # Re-run init.
    _init_minimal_project(launcher, env, project)

    second_mtime = guard.stat().st_mtime_ns
    assert first_mtime == second_mtime, (
        f"identical-bytes re-init must not rewrite the file; "
        f"mtime changed: {first_mtime} → {second_mtime}"
    )


# ---------------------- Test C: self update refresh ----------------------


# HATS-695: the --force-downgrade self update is a real pip install that times
# out under the -n8 gate's 300s budget on a slow/degraded network. Quarantined
# (HATS-694) to unblock the v0.8.0 gate; still runs solo and passes. Un-quarantine
# once HATS-695 makes it network-resilient.
@pytest.mark.quarantine
@pytest.mark.integration
@pytest.mark.pip_heavy  # HATS-678: private_launcher build is a real pip install
def test_e2e_self_update_refreshes_hook_after_drift(
    private_launcher, tmp_path
):
    """Hand-edit a materialized hook → ``self update`` restores it.

    Drives the ``Assembler.bump`` → ``_materialize_pretooluse_hooks``
    refresh path. Fail-under-revert: drop the call in
    ``Assembler.bump`` and this stays drifted.

    LONE MUTATOR (HATS-582): runs ``self update --force-downgrade`` which
    reinstalls into the pinned venv, so it uses :func:`private_launcher`
    (its own build) instead of the session-shared venv.
    """
    launcher, env, venv = private_launcher
    project = tmp_path / "proj_self_update_refresh"
    _init_minimal_project(launcher, env, project)

    hooks_dir = _materialized_hooks_dir(project)
    guard = hooks_dir / "pre_bash_shared_state_guard.sh"
    original_bytes = guard.read_bytes()
    drifted = b"#!/usr/bin/env bash\n# tampered\nexit 0\n"
    assert drifted != original_bytes

    guard.write_bytes(drifted)
    # Preserve +x just in case the user kept it executable.
    guard.chmod(0o755)
    assert guard.read_bytes() == drifted

    # No-op pip path: same git SHA → skip_install branch in
    # cli.maintenance.update. Bump still runs through _bump_internal
    # subprocess, exercising the refresh. --force-downgrade required
    # because this dev env is ahead of origin/master (see fixture).
    # HATS-673: timeout 180 (not 120) — this is the no-op skip_install
    # path (no pip), but the _bump_internal subprocess + composition
    # still run under the gate's -n8 CPU contention, so widen the margin
    # cheaply. Stays well under the pip-path 300s ceiling above.
    _run(
        [str(launcher), "self", "update", "--force-downgrade"],
        cwd=project, env=env, timeout=180,
    )

    restored = guard.read_bytes()
    assert restored == original_bytes, (
        "self update must restore the drifted hook to package-data bytes; "
        f"first 80 bytes of restored:\n{restored[:80]!r}"
    )
    assert stat.S_IMODE(guard.stat().st_mode) == 0o755


# ---------------------- Test D: safety net live ----------------------


@pytest.mark.integration
def test_e2e_materialized_hook_blocks_irreversible_no_tty(
    installed_launcher, tmp_path
):
    """Materialized hook returns exit 2 for irreversible commands w/o TTY.

    This is the proof-of-life contract for HATS-437: without
    materialization, settings.json points at a missing file and the
    safety net is silently dead. After HATS-467 lands, the hook is
    executable AND functional — given a tool_input JSON matching the
    classifier's irreversible patterns, it exits 2 (deny).

    Fail-under-revert: drop _materialize_pretooluse_hooks() and the
    script does not exist; the bash invocation here would fail with
    "No such file or directory" (exit 127) instead of the contract's
    exit 2.
    """
    launcher, env, _venv = installed_launcher
    project = tmp_path / "proj_safety_net_live"
    _init_minimal_project(launcher, env, project)

    hooks_dir = _materialized_hooks_dir(project)
    guard = hooks_dir / "pre_bash_shared_state_guard.sh"
    assert guard.is_file(), "precondition: materialize must have run"

    # Tool-input JSON the classifier recognises as irreversible.
    # The classifier sees the literal command and matches the
    # PR-merge pattern → "irreversible" → no TTY → exit 2.
    payload = json.dumps({
        "tool_input": {
            "command": "gh pr merge 42 --merge --delete-branch",
        },
    })

    env_no_ack = {k: v for k, v in env.items() if k != "AI_HATS_SHARED_STATE_ACK"}
    result = subprocess.run(
        ["bash", str(guard)],
        input=payload,
        cwd=str(project),
        env=env_no_ack,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 2, (
        f"hook must deny irreversible command in non-TTY context with "
        f"exit 2; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "shared-state-guard" in (result.stdout + result.stderr).lower(), (
        f"hook stderr should mention 'shared-state-guard'; got:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
