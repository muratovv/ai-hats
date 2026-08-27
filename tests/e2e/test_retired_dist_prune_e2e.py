"""e2e (HATS-1497)

flow: a developer running self update after a framework package dependency has been
      retired
cmds:
    ai-hats self update
expect: self update creates versioned venv without retired package and prunes retired
        console
        scripts from legacy venv
why: without retired distribution pruning, deprecated package binaries persist in
     managed venvs and
        shadow updated commands"""

from __future__ import annotations
from _helpers.git import git

import json
import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.venv import network_available, venv_unavailable
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import AI_HATS_PROJECT_DIR_ENV, ENV_AI_HATS_VENV

# HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS.
# Skipped since HATS-1521: no commit can be both older than the retirement and pin
# >=3.13, so this baseline is unreachable, not merely stale. Follow-up — HATS-1527.
pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.skip(reason="HATS-1527: pre-retirement baseline pins 3.11 (HATS-1521 floor bump)"),
]


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


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _clone_pair(tmp_path: Path) -> tuple[Path, Path]:
    """The two clones the module docstring calls for: pre-retirement + working tree.

    TWO clones, not one rewound clone — see the first test for why (workspace
    members install editable; rewinding the source dangles their ``.pth``).
    Asserts the red baseline of the *sources*: only ``src-old`` declares the
    retired dist, only ``src-new`` has retired it.
    """
    src_old = tmp_path / "src-old"
    src_new = tmp_path / "src-new"
    for clone, ref in ((src_old, PRE_RETIREMENT_REF), (src_new, "HEAD")):
        subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(clone)], check=True)
        git(clone, "config", "user.email", "e2e@test")
        git(clone, "config", "user.name", "E2E")
        git(clone, "checkout", "-B", "e2e-main", ref)  # align ls-remote HEAD
    assert RETIRED_DIST in (src_old / "pyproject.toml").read_text(), (
        f"{PRE_RETIREMENT_REF[:12]} does not depend on {RETIRED_DIST} — wrong ref; "
        "the install below would carry nothing to prune"
    )
    assert RETIRED_DIST not in (src_new / "pyproject.toml").read_text(), (
        f"the working tree still depends on {RETIRED_DIST} — there is no retirement "
        "to synchronise and this test has no subject"
    )
    return src_old, src_new


# Queried in the TARGET interpreter, not this one: "is the dist installed" is a
# property of that venv's site-packages. Emits json on one line.
_DIST_PROBE = """
import json, sys
import importlib.metadata as md

try:
    dist = md.distribution(sys.argv[1])
except md.PackageNotFoundError:
    print(json.dumps({"installed": False}))
    raise SystemExit(0)
try:
    raw = dist.read_text("direct_url.json") or ""
except OSError:
    raw = ""
editable = False
if raw:
    try:
        editable = bool((json.loads(raw).get("dir_info") or {}).get("editable"))
    except ValueError:
        editable = False
print(json.dumps({"installed": True, "version": dist.version, "editable": editable}))
"""


def _probe_dist(python_exe: Path, dist: str, env: dict[str, str]) -> dict:
    """``{installed, version?, editable?}`` for ``dist`` as that interpreter sees it."""
    assert python_exe.is_file(), f"interpreter missing at {python_exe}"
    result = subprocess.run(
        [str(python_exe), "-c", _DIST_PROBE, dist],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"dist probe for {dist!r} failed in {python_exe}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def _module_importable(python_exe: Path, module: str, env: dict[str, str]) -> bool:
    """Can that interpreter import ``module``? Identifies WHICH TREE is installed."""
    assert python_exe.is_file(), f"interpreter missing at {python_exe}"
    return (
        subprocess.run(
            [str(python_exe), "-c", f"import {module}"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        ).returncode
        == 0
    )


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
        git(clone, "config", "user.email", "e2e@test")
        git(clone, "config", "user.name", "E2E")
        # `e2e-main` aligns the clone's symbolic HEAD (what the edge resolver
        # reads via `git ls-remote HEAD`) with the checked-out tree.
        git(clone, "checkout", "-B", "e2e-main", ref)
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


@pytest.mark.integration
def test_prune_is_idempotent(tmp_path: Path) -> None:
    """A SECOND managed update converges cleanly — no crash, nothing resurrected.

    The prune runs on every ``self update`` (it is unconditional in
    ``_bump_internal``), so the run after the fixing one must be a no-op rather
    than an error. What the second update actually does differs from the first:
    it runs FROM ``versions/<sha>`` so ``reclaim_legacy_venv`` fires and the
    legacy ``.venv`` is discarded wholesale. Asserting "``.venv/bin/…`` is gone"
    would therefore be vacuous — so the assertion is the reclaim itself (which
    doubles as proof this really was the second update) plus: the retired script
    exists NOWHERE under ``<ai_hats_dir>``, and the versioned venv the tool now
    runs on never acquired the retired distribution.
    """  # comment-length: allow
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the launcher venv")

    src_old, src_new = _clone_pair(tmp_path)
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)
    sha_new = _head_sha(src_new)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_old)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop(ENV_AI_HATS_VENV, None)  # a pin routes off the managed path entirely
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
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
    assert (legacy_venv / "bin" / RETIRED_DIST).is_file(), (
        f"RED BASELINE BROKEN: the pre-retirement install ({PRE_RETIREMENT_REF[:12]}) "
        f"carries no {RETIRED_DIST} console script — there is nothing to prune and "
        "nothing for the second update to be idempotent about"
    )

    # ----- update #1: the fixing one (covered in full by the test above) -----
    env[ENV_REPO_URL] = str(src_new)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    assert (versions / "current").read_text().strip() == sha_new
    assert not (legacy_venv / "bin" / RETIRED_DIST).exists(), (
        "update #1 did not prune — the idempotence question below is moot"
    )

    # ----- update #2: same source, same sha — must converge, not fail -----
    second = _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    assert (versions / "current").read_text().strip() == sha_new, (
        "the second update moved current off the sha it was already on"
    )
    assert (versions / sha_new / ".complete").is_file(), (
        "the second update left the versioned install incomplete"
    )
    assert not legacy_venv.exists(), (
        "the second update ran from versions/<sha>, so reclaim_legacy_venv must "
        "have discarded the legacy .venv. It survives → this run did NOT take the "
        f"path this test is about.\nstdout:\n{second.stdout}\nstderr:\n{second.stderr}"
    )

    # No resurrection anywhere the launcher could still resolve a venv from.
    # (The discarded .venv lives on under AI_HATS_TRASH_DIR, outside this tree.)
    survivors = sorted(str(p) for p in ai_hats_dir.rglob(f"bin/{RETIRED_DIST}"))
    assert not survivors, f"the retired console script came back at: {survivors}"
    versioned = _probe_dist(versions / sha_new / "bin" / "python", RETIRED_DIST, env)
    assert versioned["installed"] is False, (
        f"the versioned venv the tool now runs on carries {RETIRED_DIST} "
        f"({versioned}) — the update re-installed a distribution it retired"
    )


@pytest.mark.integration
def test_inplace_upgrade_prunes_the_distribution(tmp_path: Path) -> None:
    """On the LEGACY IN-PLACE path the retired dist is uninstalled, not just stripped.

    The asymmetry the module docstring states: in-place, the venv being upgraded
    is the one that ends up holding the NEW ai-hats, whose metadata no longer
    declares the dep — so ``prune_running_interpreter`` can remove it outright
    (``strip_retired_scripts``' console-script-only compromise exists solely for
    the OTHER venv, the one left on the old ai-hats).

    Forcing the path: ``_is_managed_install`` is ``not editable AND venv is
    <ai_hats_dir>/.venv | versions/<sha>``. So the venv must be an
    ``AI_HATS_VENV`` override (outside the project) AND non-editable — the
    ``uv pip install -e`` route used by ``test_self_update_revision`` would land
    on the ``editable_install_root`` early-return in ``prune_retired`` and prove
    nothing. The launcher refuses to create an override venv (user-owned), so it
    is built here exactly as ``heal_if_needed`` would: ``uv venv`` + a
    non-editable install of the pre-retirement source.
    """  # comment-length: allow
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the launcher venv")

    src_old, src_new = _clone_pair(tmp_path)
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    override_venv = tmp_path / "override-venv"  # OUTSIDE the project → not managed
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_old)
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env[ENV_AI_HATS_VENV] = str(override_venv)
    env.pop(AI_HATS_PROJECT_DIR_ENV, None)  # else the launcher drops the pin as foreign
    env.pop("PYTHONPATH", None)
    venv_python = override_venv / "bin" / "python"
    retired_script = override_venv / "bin" / RETIRED_DIST

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    # ----- 1. pre-retirement install into the override venv -----
    _run(["uv", "venv", "--python", "3.13", str(override_venv)], cwd=tmp_path, env=env, timeout=300)
    _run(
        ["uv", "pip", "install", "--python", str(venv_python), str(src_old)],
        cwd=tmp_path,
        env=env,
        timeout=600,
    )
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
    versions = ai_hats_dir / "versions"

    # RED BASELINE — the dist itself, not merely its script.
    before = _probe_dist(venv_python, RETIRED_DIST, env)
    assert before["installed"] is True, (
        f"RED BASELINE BROKEN: {PRE_RETIREMENT_REF[:12]} installed into "
        f"{override_venv} without {RETIRED_DIST} ({before}) — with no distribution "
        "on disk the uninstall assertion below would pass vacuously"
    )
    assert retired_script.is_file(), (
        f"RED BASELINE BROKEN: {RETIRED_DIST} is installed but its console script "
        f"is absent from {override_venv}/bin"
    )
    # The prune stands down on an editable ai-hats — if the install came out
    # editable, everything below would be an early return, not a prune.
    ai_hats_before = _probe_dist(venv_python, "ai-hats", env)
    assert ai_hats_before["editable"] is False, (
        f"ai-hats installed EDITABLE into {override_venv} ({ai_hats_before}); "
        "prune_retired early-returns on editable_install_root, so this test would "
        "prove nothing — the setup must install non-editable"
    )
    assert not versions.exists() and not (ai_hats_dir / ".venv").exists(), (
        "a managed venv layout exists already; the update below could route "
        "through the blue-green path instead of the legacy in-place one"
    )

    # ----- 2. one in-place `self update` onto the working tree -----
    env[ENV_REPO_URL] = str(src_new)
    result = _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=600)
    combined = result.stdout + result.stderr

    # ----- the path this test claims to exercise, proven three ways -----
    assert not versions.exists() and not (ai_hats_dir / ".venv").exists(), (
        "the update built a managed venv layout — it took the blue-green path, "
        f"not the legacy in-place one:\n{combined}"
    )
    ai_hats_after = _probe_dist(venv_python, "ai-hats", env)
    assert ai_hats_after["version"] != ai_hats_before["version"], (
        f"ai-hats in {override_venv} is unchanged ({ai_hats_after['version']}) — the "
        f"in-place install never landed in the venv under test:\n{combined}"
    )
    assert ai_hats_after["editable"] is False, (
        "ai-hats came out editable — the update took the channel:local editable "
        f"path, where prune_retired deliberately stands down:\n{combined}"
    )
    # prune_running_interpreter reports dist NAMES; strip_retired_scripts reports
    # PATHS. A bare name here is the uninstall branch, in this interpreter.
    assert f"removed retired {RETIRED_DIST}" in combined, (
        "the bump never reported an uninstall of the retired distribution from the "
        f"running interpreter:\n{combined}"
    )

    # ----- THE GAP: uninstalled, not merely unscripted -----
    after = _probe_dist(venv_python, RETIRED_DIST, env)
    assert after["installed"] is False, (
        f"{RETIRED_DIST} is still installed in {override_venv} ({after}). On the "
        "in-place path this venv holds the NEW ai-hats, which no longer declares "
        "the dep — stripping the script is not enough, the distribution must go"
    )
    assert not retired_script.exists(), (
        f"{RETIRED_DIST} is uninstalled but its console script survives at "
        f"{retired_script} — the retired CLI is still reachable by name"
    )


@pytest.mark.integration
def test_downgrade_does_not_prune(tmp_path: Path) -> None:
    """A DOWNGRADE past the retirement leaves ``ai-hats-tracker`` alone.

    The invariant, named: **the prune always executes the version that was just
    installed**. ``_bump_internal`` is its only carrier and ``self update``
    invokes that hook in a fresh interpreter *inside the freshly-installed
    tree* — so installing a PRE-retirement ai-hats runs that ref's
    ``_bump_internal``, which has no prune at all. The version that issued the
    update never gets to synchronise the venv it just built.

    Shape: the mirror image of the first test — a post-retirement managed
    install, then one ``self update --force-downgrade`` with the source pointed
    back at ``PRE_RETIREMENT_REF``.

    What would make this pass for the WRONG reason, and the guard for each:

    * *the downgrade never landed* → ``current`` and ``.complete`` are asserted
      on ``sha_old`` (≠ ``sha_new``), and the freshly-installed tree is proven
      to be the pre-retirement one by the absence of ``ai_hats.retired_dists``;
    * *the bump hook never fired, so nothing could have pruned either way* →
      the ``Re-assembling`` banner is asserted; it is printed immediately before
      the hook subprocess and only when the hook runs;
    * *the retired dist was never installed to begin with* → that IS the
      assertion, so it fails loud rather than vacuously.

    Fail-under-rewiring: move the prune out of the installed tree and into the
    OUTGOING updater aimed at the venv it just built (the obvious "synchronise
    what we installed" alternative) and ``versions/<sha_old>`` loses the
    tracker — both final assertions go red.
    """  # comment-length: allow
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the launcher venv")

    src_old, src_new = _clone_pair(tmp_path)
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)
    sha_old = _head_sha(src_old)
    sha_new = _head_sha(src_new)
    assert sha_old != sha_new, "both clones resolve to the same sha — no version moves here"

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_new)  # start POST-retirement, unlike the tests above
    env["AI_HATS_TRASH_DIR"] = str(tmp_path / "trash")
    env.pop(ENV_AI_HATS_VENV, None)  # a pin routes off the managed path entirely
    env.pop("PYTHONPATH", None)

    # ----- 1. post-retirement install (no tracker anywhere) -----
    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
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

    assert legacy_venv.is_dir(), f"launcher did not bootstrap the venv at {legacy_venv}"
    assert not versions.exists(), (
        "versions/ already exists — the update below would not be the first one"
    )
    # The starting point is the mirror of the other tests: nothing to prune yet.
    # Anything the assertions find at the end was installed BY the downgrade.
    assert not (legacy_venv / "bin" / RETIRED_DIST).exists(), (
        f"the post-retirement install already carries {RETIRED_DIST} — the "
        "working tree has not actually retired it and this test has no subject"
    )
    assert _module_importable(legacy_venv / "bin" / "python", "ai_hats.retired_dists", env), (
        "the installed working tree has no ai_hats.retired_dists — the prune "
        "does not ship in the version issuing the downgrade, so 'it did not run' "
        "below would be true for a reason this test is not about"
    )

    # ----- 2. one managed downgrade to the pre-retirement ref -----
    # --force-downgrade waives the edge ahead/diverged guard — the flag a user
    # downgrading actually passes.
    env[ENV_REPO_URL] = str(src_old)
    result = _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project,
        env=env,
        timeout=600,
    )
    combined = result.stdout + result.stderr

    # ----- the downgrade landed, and the hook that carries the prune fired -----
    assert (versions / "current").read_text().strip() == sha_old, (
        f"current did not move to the pre-retirement sha {sha_old[:12]} — no "
        f"downgrade happened, so nothing below is about one:\n{combined}"
    )
    assert (versions / sha_old / ".complete").is_file(), (
        f"the downgrade left versions/{sha_old[:12]} incomplete:\n{combined}"
    )
    old_python = versions / sha_old / "bin" / "python"
    assert not _module_importable(old_python, "ai_hats.retired_dists", env), (
        f"versions/{sha_old[:12]} can import ai_hats.retired_dists — the tree "
        "that was just installed is NOT the pre-retirement one, so its "
        f"_bump_internal is not the prune-less one this test relies on:\n{combined}"
    )
    assert "Re-assembling" in combined, (
        "the update never ran the fresh-interpreter bump — that hook is the "
        "prune's only call site, so with it skipped this test could not tell a "
        f"stood-down prune from an absent one:\n{combined}"
    )

    # ----- THE INVARIANT: the downgrade produced a coherent OLD install -----
    tracker = _probe_dist(old_python, RETIRED_DIST, env)
    assert tracker["installed"] is True, (
        f"{RETIRED_DIST} is not installed in versions/{sha_old[:12]} ({tracker}). "
        f"The pre-retirement ref declares it, so the downgrade either failed or "
        "something pruned a dependency the just-installed version still needs — "
        f"the prune must run FROM the installed tree, never AT it:\n{combined}"
    )
    retired_script = versions / sha_old / "bin" / RETIRED_DIST
    assert retired_script.is_file(), (
        f"{RETIRED_DIST} is installed in versions/{sha_old[:12]} but its console "
        f"script is missing from {retired_script.parent} — the downgraded install "
        f"was gutted rather than left alone:\n{combined}"
    )
    cli = subprocess.run(  # noqa: S603 - fixed argv, path built by this test
        [str(retired_script), "--help"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert cli.returncode == 0 and RETIRED_DIST in cli.stdout, (
        f"the retired CLI survives on disk but does not run "
        f"(exit {cli.returncode})\nstdout:\n{cli.stdout}\nstderr:\n{cli.stderr}"
    )


@pytest.mark.integration
def test_upgrade_completes_when_the_prune_raises(tmp_path: Path) -> None:
    """A prune that BLOWS UP mid-removal still cannot fail the managed update.

    The unit suite proves ``prune_retired`` swallows everything; this proves the
    WIRING does too — ``.complete`` written, ``versions/current`` flipped, exit
    code 0, and the bump's own exit code untouched.

    How the prune is made to fail: on the managed path its only actionable
    target is ``strip_retired_scripts`` against the legacy ``.venv`` (the
    freshly built ``versions/<sha>`` never carries the retired dist, so
    ``prune_running_interpreter`` has nothing to uninstall there and a
    broken/absent ``uv`` would be a silent no-op — besides breaking the
    update's own install first). So the legacy ``.venv/bin`` is made
    **read-only** for the duration of the update: ``discard``'s
    ``shutil.move`` fails to rename, falls back to copy-then-unlink, the copy
    lands in the trash and the unlink raises ``PermissionError`` — which
    ``strip_retired_scripts`` swallows per path.

    The vacuity trap this test must dodge: "the retired script is still there"
    is ALSO what you would see if the prune never ran at all — then the fail-open
    claim would be untested. The trash copy is the disambiguator: only
    ``discard`` puts an ``ai-hats-tracker`` under ``AI_HATS_TRASH_DIR``, and
    only ``strip_retired_scripts`` calls ``discard`` on that path. Copy present
    + original present == the prune ran, reached the removal, and failed. The
    red baseline (script on disk before the update) and the ``Re-assembling``
    banner close the two remaining vacuity holes.
    """  # comment-length: allow
    if not network_available():
        venv_unavailable("uv not on PATH — cannot build the launcher venv")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("running as root — file mode bits do not restrict the delete")

    src_old, src_new = _clone_pair(tmp_path)
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    trash_dir = tmp_path / "trash"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)
    sha_new = _head_sha(src_new)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_old)
    env["AI_HATS_TRASH_DIR"] = str(trash_dir)
    env.pop(ENV_AI_HATS_VENV, None)  # a pin routes off the managed path entirely
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
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
    bin_dir = legacy_venv / "bin"
    retired_script = bin_dir / RETIRED_DIST

    assert not versions.exists(), (
        "versions/ already exists — this would not be the FIRST managed update, "
        "and the legacy .venv the sabotage targets would be reclaimed instead of "
        "pruned"
    )
    # RED BASELINE — without a script on disk the prune has nothing to attempt
    # and every assertion below is satisfied by an inert no-op.
    assert retired_script.is_file(), (
        f"RED BASELINE BROKEN: the pre-retirement install ({PRE_RETIREMENT_REF[:12]}) "
        f"carries no {RETIRED_DIST} console script — the prune would have nothing "
        "to fail at"
    )

    # ----- sabotage: the removal cannot complete, the detection still can -----
    # 0o500 keeps r-x (so `is_file()` and the running interpreter's own
    # bin/python are unaffected) and drops w (so unlink/rename inside fail).
    env[ENV_REPO_URL] = str(src_new)
    bin_dir.chmod(0o500)
    try:
        result = _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=600)
    finally:
        bin_dir.chmod(0o755)  # never hand pytest a tmp tree it cannot clean up
    combined = result.stdout + result.stderr

    # ----- the update completed anyway: sentinel, pointer, exit code -----
    # (`_run` already asserted exit 0 — a failed install exits 1, a lock
    # contention 2.)
    assert (versions / sha_new / ".complete").is_file(), (
        f"the sentinel is missing — the update did not complete:\n{combined}"
    )
    assert (versions / "current").read_text().strip() == sha_new, (
        f"current was not flipped to {sha_new[:12]} — the update did not complete:\n{combined}"
    )
    assert "Bump (fresh interpreter)" not in combined, (
        "the bump reported a non-zero exit — the prune's failure leaked into the "
        f"exit code it is required to stay out of:\n{combined}"
    )
    assert "Re-assembling" in combined, (
        "the fresh-interpreter bump never ran, so the prune never got a chance "
        f"to fail and the fail-open claim is untested:\n{combined}"
    )

    # ----- PROOF the prune ran and FAILED (not that it had nothing to do) -----
    assert retired_script.is_file(), (
        f"{retired_script} is gone — the sabotage did not take, so this run "
        "exercised the ordinary (successful) prune, not the failing one"
    )
    trashed = sorted(str(p) for p in trash_dir.rglob(RETIRED_DIST) if p.parent.name == "bin")
    assert trashed, (
        f"no {RETIRED_DIST} copy under {trash_dir}: strip_retired_scripts never "
        f"reached discard(), so the prune did not run at all and 'the script "
        f"survived' proves nothing about fail-open:\n{combined}"
    )
