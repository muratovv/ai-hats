"""E2E: ``ai-hats self update --revision <REF>`` (HATS-496).

Verifies three behaviours of the new ``--revision`` flag in one test to
amortize the heavy setup (~3 min: clone + launcher install + pip
bootstrap + pip --force-reinstall at a real tag):

  1. **D2 editable protection.** ``--revision <TAG>`` against an editable
     target venv refuses with exit 2 and a message that names
     ``editable install`` + ``--force``. pip is not invoked.
  2. **Pre-flight ref validation.** ``--revision <bogus> --force`` refuses
     with exit 2 and ``not found on remote``, before pip runs. Install
     state must remain editable (proves pip was skipped).
  3. **Happy path.** ``--revision <TAG> --force`` installs the pinned ref,
     replacing the editable install. ``direct_url.json`` reflects the
     literal ref in ``vcs_info.requested_revision`` plus a resolved
     ``commit_id`` (PEP 610).

Setup contract (real subprocess + real pip):

  - ``src-repo``  — clone of REPO_ROOT (carries all local tags so the
                    file:// URL can serve refs via ``git ls-remote``).
  - ``project``   — fresh project dir; launcher resolves venv to
                    ``<project>/.agent/ai-hats/.venv`` per its default
                    precedence (no ``AI_HATS_VENV`` exported).
  - **editable conversion** — bootstrap.sh installs ai-hats *non-*editable
                    from a ``file://`` URL (the default launcher flow). For
                    D2 to mean anything we must convert to editable via
                    ``pip uninstall && pip install -e <src-repo>``, mirroring
                    the established pattern in
                    ``test_self_update_downgrade_gate.py`` (which needed
                    editable for a different reason — ``.git`` reachable
                    from ``ai_hats.__file__``).

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Fail-under-revert: if the ``--revision`` plumbing is removed from
``cli/maintenance.py``, the assertion 1 invocation receives a click
``unknown option`` error (exit 2 but stderr says "no such option"),
which the substring assertion on ``editable install`` catches.

Deliberate long e2e scenario contract — noqa: comment-length.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0, check_returncode=True):
    """Run subprocess; assert exit code on demand."""
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env,
        capture_output=True, text=True, timeout=timeout,
    )
    if check_returncode and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _find_direct_url(venv_dir: Path) -> Path:
    """Return the ai-hats dist-info ``direct_url.json`` under the venv.

    Raises ``AssertionError`` if absent — a clean ai-hats install always
    writes one (PEP 610).
    """
    matches = list(
        venv_dir.glob("lib/python*/site-packages/ai_hats-*.dist-info/direct_url.json")
    )
    assert matches, f"direct_url.json missing under {venv_dir}"
    return matches[0]


@pytest.mark.integration
def test_e2e_self_update_revision(tmp_path: Path) -> None:
    """End-to-end: --revision pin install, with D2 editable + ref-validation gates.

    Three assertions amortize the heavy setup (~3 min total).
    """
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # HATS-764: edge so the bootstrap self update resolves the local source

    # ----- fixture: src-repo (clone of REPO_ROOT, carries all tags) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )

    # Pick the latest reachable tag from src-repo's HEAD. Hard-coding a
    # specific tag (e.g. ``v0.7.0``) would couple the test to a single
    # release line; ``describe --abbrev=0`` follows the live tag tip.
    tag_probe = subprocess.run(
        ["git", "-C", str(src_repo), "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True,
    )
    if tag_probe.returncode != 0 or not tag_probe.stdout.strip():
        pytest.skip("no git tags available in src-repo")
    pinned_tag = tag_probe.stdout.strip()

    # ----- bootstrap: launcher + editable venv from src-repo -----
    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env.pop("AI_HATS_VENV", None)
    # PYTHONPATH from the test runner can shadow the venv's editable
    # install by adding the worktree's ``src/`` to sys.path ahead of
    # site-packages. The subprocess MUST resolve ``ai_hats`` from the
    # project venv only.
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=300)  # HATS-675: 300s = -n8 gate suite norm

    venv_dir = project / ".agent" / "ai-hats" / ".venv"
    assert venv_dir.is_dir(), f"venv missing at {venv_dir}"

    # Launcher bootstrap installs from ``file://`` non-editable by default
    # (direct_url.json has dir_info={} — file URL but no editable marker).
    # Convert to editable so the D2 path is reachable in assertion 1.
    # HATS-763: uv venvs ship no pip — convert via uv, targeting the venv interp.
    venv_python = venv_dir / "bin" / "python"
    assert venv_python.is_file(), f"project venv python missing at {venv_python}"
    subprocess.run(
        ["uv", "pip", "uninstall", "--python", str(venv_python), "ai-hats"],
        env=env, check=True, timeout=60,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), "-e", str(src_repo)],
        env=env, check=True, timeout=180,
    )
    # HATS-647: the non-editable bootstrap `self update` created a versions/<sha>/
    # + current pointer; drop it so the launcher resolves the now-editable .venv
    # via default precedence (this test exercises the editable/legacy path).
    shutil.rmtree(project / ".agent" / "ai-hats" / "versions", ignore_errors=True)
    initial = json.loads(_find_direct_url(venv_dir).read_text())
    assert initial.get("dir_info", {}).get("editable") is True, (
        f"editable conversion did not take effect; assertion 1 (D2) would "
        f"not exercise the editable-refusal path. direct_url={initial!r}"
    )

    # ----- swap AI_HATS_REPO_URL to git+file:// for the --revision tests -----
    # The launcher bootstrap above used the bare path (which pip resolves as
    # a plain directory install, no git semantics). --revision requires a
    # git URL: pip clones, checks out the ref, builds. ``git+file://`` is
    # the canonical scheme for local git repos and is what pip + ``git
    # ls-remote`` (with the ``git+`` prefix stripped) both understand.
    env["AI_HATS_REPO_URL"] = f"git+file://{src_repo}"

    # ----- assertion 1: D2 — editable + --revision WITHOUT --force → refuse -----
    a1 = _run(
        [str(launcher_dest), "self", "update", "--revision", pinned_tag],
        cwd=project, env=env, timeout=60,
        expect_exit=2,
    )
    combined1 = a1.stdout + a1.stderr
    assert "editable install" in combined1, (
        f"D2 refusal message missing 'editable install':\n{combined1}"
    )
    assert "--force" in combined1, (
        f"D2 refusal missing --force hint:\n{combined1}"
    )

    # Install state unchanged (still editable — pip never ran).
    state_after_a1 = json.loads(_find_direct_url(venv_dir).read_text())
    assert state_after_a1.get("dir_info", {}).get("editable") is True, (
        f"editable install was modified despite D2 refusal: {state_after_a1!r}"
    )

    # ----- assertion 2: pre-flight ref validation — bogus ref + --force → refuse -----
    a2 = _run(
        [str(launcher_dest), "self", "update",
         "--revision", "definitely-not-a-ref-xyz123", "--force"],
        cwd=project, env=env, timeout=60,
        expect_exit=2,
    )
    combined2 = a2.stdout + a2.stderr
    assert "not found on remote" in combined2, (
        f"ref-validation message missing 'not found on remote':\n{combined2}"
    )

    # Install state still unchanged — pre-flight ran before pip.
    state_after_a2 = json.loads(_find_direct_url(venv_dir).read_text())
    assert state_after_a2.get("dir_info", {}).get("editable") is True, (
        f"install was modified despite ref-validation refusal: {state_after_a2!r}"
    )

    # ----- assertion 3: happy path — --revision <tag> --force → pinned install -----
    a3 = _run(
        [str(launcher_dest), "self", "update",
         "--revision", pinned_tag, "--force"],
        cwd=project, env=env, timeout=300,
    )
    combined3 = a3.stdout + a3.stderr
    assert "--revision bypasses" in combined3, (
        f"D1 WARN missing from output:\n{combined3}"
    )

    # direct_url.json now records the pinned ref + a resolved SHA (PEP 610).
    state_after_a3 = json.loads(_find_direct_url(venv_dir).read_text())
    assert state_after_a3.get("dir_info", {}).get("editable") is not True, (
        f"editable install was not replaced by the pinned install: "
        f"{state_after_a3!r}"
    )
    vcs = state_after_a3.get("vcs_info") or {}
    assert vcs.get("requested_revision") == pinned_tag, (
        f"requested_revision mismatch: "
        f"got {vcs.get('requested_revision')!r}, want {pinned_tag!r}"
    )
    assert vcs.get("commit_id"), (
        f"vcs_info.commit_id missing from pinned install: {state_after_a3!r}"
    )
