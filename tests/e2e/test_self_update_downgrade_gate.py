"""E2E: ``ai-hats self update`` refuses silent downgrade (HATS-441).

The bug it catches:

  Before HATS-441, ``ai-hats self update`` unconditionally ran
  ``pip install --force-reinstall git+...`` even when the installed HEAD
  was *ahead* of remote master. The result: a silent downgrade —
  ``dev77+ga0ad85058 → dev70+gbc84726c2`` without warning, replacing an
  editable install (with unpushed work) by the non-editable remote
  snapshot.

After HATS-441, the command refuses with exit code 3 unless
``--force-downgrade`` is passed.

Setup contract (real subprocess + real pip):

  - ``src-repo``  — clone of REPO_ROOT + one empty commit on top of
                    master. ``installed_sha`` resolves to this checkout's
                    HEAD via editable install.
  - ``fake-remote.git`` — bare clone of REPO_ROOT. Its master is exactly
                    one commit *behind* ``src-repo``. Used as
                    ``AI_HATS_REPO_URL`` for the probe + pip target.
  - editable install: launcher bootstrap installs from src-repo; we
                    then convert to ``pip install -e src-repo`` so the
                    package directory carries a usable ``.git`` for the
                    HATS-432 ahead/behind probe (``_fetch_into_pkg``).

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``.

Fail-under-revert: if the gate is removed from ``cli/maintenance.py``,
the refuse-case invocation succeeds (exit 0, silent downgrade) and the
``returncode == 3`` assertion below fails.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0, check_returncode=True):
    """Run a subprocess; assert exit code matches ``expect_exit`` when
    ``check_returncode`` is True."""
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


@pytest.mark.integration
def test_e2e_self_update_refuses_silent_downgrade(tmp_path: Path) -> None:
    """End-to-end: ahead state refused; ``--force-downgrade`` overrides.

    Two assertions amortize the heavy setup:

    1. ``self update`` (no flag) → exit 3 + refusal text. Proves the gate
       fires inside a real subprocess (not just ``CliRunner.invoke``).
    2. ``self update --force-downgrade`` → exit 0 + override warning.
       Proves the new click flag is wired and bypasses the gate.
    """
    src_repo = tmp_path / "src-repo"
    fake_remote = tmp_path / "fake-remote.git"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # ----- fixture: src-repo (installed checkout, +1 commit ahead) -----
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(src_repo), "config", "user.email", "e2e@test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(src_repo), "config", "user.name", "E2E"],
        check=True,
    )
    # Capture src-repo HEAD BEFORE the +1 commit — this becomes the
    # explicit remote master target, guaranteeing a clean 1-ahead/0-behind
    # delta regardless of which branch git-clone-of-a-worktree happened
    # to follow as its default HEAD.
    pre_ahead_sha = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "-C", str(src_repo), "commit", "--allow-empty",
         "-m", "HATS-441 e2e: simulated ahead-of-remote commit"],
        check=True,
    )

    # ----- fixture: fake-remote.git (probe target — bare clone, master pinned) -----
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(REPO_ROOT), str(fake_remote)],
        check=True,
    )
    # Force fake-remote master to ``pre_ahead_sha`` — exactly one commit
    # behind src-repo HEAD. ``git clone --bare`` of a worktree may otherwise
    # leave master at the worktree's currently-checked-out branch tip,
    # which is identical to src-repo's pre-commit HEAD on the common path
    # but is not guaranteed and depends on the host environment.
    subprocess.run(
        ["git", "-C", str(fake_remote), "update-ref",
         "refs/heads/master", pre_ahead_sha],
        check=True,
    )

    # ----- bootstrap: launcher + venv (uses src-repo for first install) -----
    env = os.environ.copy()
    env["AI_HATS_LAUNCHER_DEST"] = str(launcher_dest)
    env["AI_HATS_REPO_URL"] = str(src_repo)
    env.pop("AI_HATS_VENV", None)
    # PYTHONPATH from the test runner can shadow the venv's editable install
    # by adding the worktree's ``src/`` to sys.path ahead of site-packages.
    # The subprocess MUST resolve ``ai_hats`` from the project venv only.
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    # First update populates the project venv with a working ai-hats install.
    _run([str(launcher_dest), "self", "update"],
         cwd=project, env=env, timeout=180)

    # ----- convert to editable install so the package dir carries .git -----
    # The ahead/behind probe (``_fetch_into_pkg`` / ``_count_ahead_behind``
    # in ``update_check/checker.py``) requires a git checkout reachable
    # from ``ai_hats.__file__``'s directory. Non-editable pip extracts to
    # site-packages without ``.git``; editable points ``ai_hats.__file__``
    # back at ``<src-repo>/src/ai_hats/__init__.py`` which IS tracked.
    #
    # ``pip install -e`` over an existing non-editable snapshot can be a
    # silent no-op ("already satisfied"). Force the conversion by
    # uninstalling first.
    venv_pip = project / ".agent" / "ai-hats" / ".venv" / "bin" / "pip"
    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert venv_pip.is_file(), \
        f"project venv pip missing at {venv_pip}"
    subprocess.run(
        [str(venv_pip), "uninstall", "-y", "--quiet", "ai-hats"],
        env=env, check=True, timeout=60,
    )
    subprocess.run(
        [str(venv_pip), "install", "--quiet", "-e", str(src_repo)],
        env=env, check=True, timeout=120,
    )
    # HATS-647: the non-editable bootstrap `self update` created a versions/<sha>/
    # + current pointer; drop it so the launcher resolves the now-editable .venv
    # via default precedence — otherwise the downgrade probe would run against
    # the versioned (non-editable) venv and bypass the gate this test asserts.
    shutil.rmtree(project / ".agent" / "ai-hats" / "versions", ignore_errors=True)
    # Sanity: ``ai_hats.__file__`` MUST resolve into src-repo's tree, else
    # the editable conversion is broken and the rest of the test would
    # silently exercise a non-editable install (which falls into the
    # ``__commit__`` fallback path and bypasses the gate by design).
    where = subprocess.run(
        [str(venv_python), "-c",
         "import ai_hats, pathlib; print(pathlib.Path(ai_hats.__file__).resolve())"],
        env=env, capture_output=True, text=True, check=True, timeout=15,
    ).stdout.strip()
    assert str(src_repo) in where, (
        f"editable conversion did not take effect: ai_hats.__file__={where!r}, "
        f"expected to be under src-repo={src_repo}"
    )

    # ----- swap probe target to the fake remote (behind installed by 1) -----
    env["AI_HATS_REPO_URL"] = f"git+file://{fake_remote}"

    # ----- assertion 1: gate refuses, exit 3 -----
    refuse = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project, env=env, timeout=60,
        expect_exit=3,
    )
    combined = refuse.stdout + refuse.stderr
    assert "Refusing to downgrade" in combined, (
        f"refusal message missing; combined output:\n{combined}"
    )
    assert "--force-downgrade" in combined, (
        f"override hint missing; combined output:\n{combined}"
    )

    # ----- assertion 2: --force-downgrade override succeeds, exit 0 -----
    override = _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project, env=env, timeout=300,
    )
    combined2 = override.stdout + override.stderr
    assert "--force-downgrade bypasses" in combined2, (
        f"override warning missing; combined output:\n{combined2}"
    )
    assert "Refusing to downgrade" not in combined2, (
        f"refusal printed despite --force-downgrade; combined:\n{combined2}"
    )
