"""E2E (HATS-1280): a managed ``self update`` leaves a retired distribution behind.

What this pins: 0.14.0 dropped the ``ai-hats-tracker`` dependency, but an update
installs without synchronising — the retired distribution and its
``[project.scripts] ai-hats-tracker`` console entry survive in the venv that
still carries them. On the managed (blue-green) path the upgrade builds a
*fresh* ``versions/<sha>/`` (nothing stale to find there), while the
pre-versioning legacy ``<ai_hats_dir>/.venv`` keeps a working
``bin/ai-hats-tracker``.

Why the FIRST update is the load-bearing case: ``reclaim_legacy_venv``
(``version_recovery.py``) returns early while the updater is itself running
from ``.venv`` (``current_run_sha`` is None), so the first update keeps that
directory verbatim — stale scripts and all. The SECOND update discards the
whole directory, which would make a naive "``.venv`` is gone" assertion pass
with no fix in place and prove nothing. Hence: exactly ONE update, and the
assertion is "``.venv`` survives, its retired console script does not".

Shape: bootstrap the pre-versioning install from the last ref that still ships
``packages/ai-hats-tracker`` (``self init`` → the launcher's heal builds
``.venv``; no ``versions/`` yet), advance the install source to the working
tree (the version that retired the dist), then one managed ``self update``.

Fail-under-revert: revert the prune and ``.venv/bin/ai-hats-tracker`` is still
on disk after the update — the final assertion fails.

Deliberate long contract module docstring — noqa: comment-length.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.venv import network_available, venv_unavailable
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV

pytestmark = (
    pytest.mark.install_heavy
)  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

# ``710f45d3^`` — the last commit still depending on ai-hats-tracker (710f45d3
# deleted ``packages/ai-hats-tracker``). Asserted below, so a wrong ref fails
# loud instead of leaving nothing to prune.
PRE_RETIREMENT_REF = "5cb14c5cadb46313f223927a7a699d6db64ef259"
RETIRED_DIST = "ai-hats-tracker"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY → `self init` takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _git(args, cwd):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.mark.integration
def test_e2e_first_managed_update_prunes_retired_console_script(tmp_path: Path) -> None:
    """One managed update off a pre-retirement install must leave the legacy
    ``.venv`` in place but strip the retired dist's console script from it."""
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the launcher venv")

    # TWO clones, not one rewound clone: workspace members install EDITABLE, so
    # rewinding the source dangles ai_hats_tracker's .pth and the old CLI then
    # loops forever in `_bootstrap.bootstrap_or_die` (install, re-exec, repeat).
    src_old = tmp_path / "src-old"  # pre-retirement — stays on disk, keeps the .pth live
    src_new = tmp_path / "src-new"  # the working tree — the version that retired the dist
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so the update resolves the local source

    for clone, ref in ((src_old, PRE_RETIREMENT_REF), (src_new, "HEAD")):
        subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(clone)], check=True)
        _git(["config", "user.email", "e2e@test"], clone)
        _git(["config", "user.name", "E2E"], clone)
        # `e2e-main` aligns the clone's symbolic HEAD (what the edge resolver
        # reads via `git ls-remote HEAD`) with the checked-out tree.
        _git(["checkout", "-B", "e2e-main", ref], clone)
    sha_new = _head_sha(src_new)

    assert RETIRED_DIST in (src_old / "pyproject.toml").read_text(), (
        f"{PRE_RETIREMENT_REF[:12]} does not depend on {RETIRED_DIST} — wrong ref; "
        "the install below would carry nothing to prune"
    )
    assert RETIRED_DIST not in (src_new / "pyproject.toml").read_text(), (
        f"the working tree still depends on {RETIRED_DIST} — there is no retirement "
        "to synchronise and this test has no subject"
    )

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_old)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")  # keep discards sandboxed
    # CRITICAL: an AI_HATS_VENV pin routes off the managed path entirely
    # (`maintenance._is_managed_install` keys off sys.prefix); PYTHONPATH would
    # shadow the install with this checkout's source.
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    # ----- 1. pre-retirement install -----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    # `self init` reproduces the PRE-VERSIONING shape: the launcher's heal builds
    # <ai_hats_dir>/.venv and init itself never touches the venv (HATS-1215), so
    # no versions/ exists yet — exactly the install an old user updates from.
    _run(
        [
            str(launcher_dest),
            "self",
            "init",
            "-r",
            "assistant",
            "-p",
            "claude",
            "--no-wizard",
            "--channel",
            "edge",
        ],
        cwd=project,
        env=env,
        timeout=300,
    )

    ai_hats_dir = project / ".agent" / "ai-hats"
    legacy_venv = ai_hats_dir / ".venv"
    versions = ai_hats_dir / "versions"
    retired_script = legacy_venv / "bin" / RETIRED_DIST

    assert legacy_venv.is_dir(), f"launcher did not bootstrap the legacy venv at {legacy_venv}"
    assert not versions.exists(), (
        "expected the pre-versioning shape (legacy .venv only); versions/ already "
        "exists, so the update below would not be the FIRST one"
    )
    # RED BASELINE — everything downstream is vacuous without it.
    assert retired_script.is_file(), (
        f"RED BASELINE BROKEN: the pre-retirement install ({PRE_RETIREMENT_REF[:12]}) "
        f"was supposed to put {RETIRED_DIST} in {legacy_venv}/bin, but it is absent. "
        "With no retired script on disk the gap assertion below would pass "
        "vacuously and this test would prove nothing — fix the setup, not the "
        "assertion."
    )

    # ----- 2. exactly ONE managed self update, onto the working tree -----
    env[ENV_REPO_URL] = str(src_new)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    assert (versions / "current").read_text().strip() == sha_new, (
        "managed blue-green update did not land — the retired-dist question is moot"
    )
    assert (versions / sha_new / ".complete").is_file(), "versioned install incomplete"
    assert legacy_venv.is_dir(), (
        "legacy .venv must still exist after the FIRST update (the updater ran from "
        "it; reclaim_legacy_venv's current_run_sha guard skips) — if it is gone, this "
        "run was not the first update and the assertion below proves nothing"
    )

    # ----- THE GAP -----
    assert not retired_script.exists(), (
        f"{RETIRED_DIST} was retired from the dependency graph, but its console "
        f"script survives the update at {retired_script} — `self update` installs "
        "without synchronising the venv it left behind"
    )
