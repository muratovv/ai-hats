"""e2e (HATS-441, HATS-766)

flow:   a developer running self update on edge channel when local commits are ahead
        of remote
cmds:
    ai-hats self update
expect: edge channel update guard prevents downgrading unpushed local edge commits to
        remote origin
why:    without custom edge guards, self update overwrites unpushed local feature commits
        with remote origin
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from ai_hats.paths import ENV_AI_HATS_VENV
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0, check_returncode=True):
    """Run a subprocess; assert exit code matches ``expect_exit`` when set."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check_returncode and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_edge_guard_probes_custom_repo_default_branch(tmp_path: Path) -> None:
    """A custom edge repo with a non-master default branch still triggers the guard.

    Proves the probe follows the edge repo's ``HEAD`` (default branch) rather
    than a hardwired ``master`` the repo may not have.
    """
    src_repo = tmp_path / "src-repo"
    fake_remote = tmp_path / "fake-remote.git"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    pin_edge_channel(project)  # edge so self update resolves the local AI_HATS_REPO_URL

    # ----- fixture: src-repo (installed checkout, +1 commit ahead) -----
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    subprocess.run(["git", "-C", str(src_repo), "config", "user.email", "e2e@test"], check=True)
    subprocess.run(["git", "-C", str(src_repo), "config", "user.name", "E2E"], check=True)
    pre_ahead_sha = subprocess.run(
        ["git", "-C", str(src_repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        [
            "git",
            "-C",
            str(src_repo),
            "commit",
            "--allow-empty",
            "-m",
            "HATS-766 e2e: simulated ahead-of-edge commit",
        ],
        check=True,
    )

    # ----- fixture: fake-remote.git with default branch `trunk` (NO master) -----
    # The non-master default is the whole point: the hardwired-`master` revert
    # finds no ref here, so only the HEAD-following fix produces a verdict.
    subprocess.run(
        ["git", "clone", "--quiet", "--bare", str(REPO_ROOT), str(fake_remote)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fake_remote), "update-ref", "refs/heads/trunk", pre_ahead_sha],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(fake_remote), "symbolic-ref", "HEAD", "refs/heads/trunk"],
        check=True,
    )
    # Delete master so a hardwired-`master` probe resolves to nothing.
    subprocess.run(
        ["git", "-C", str(fake_remote), "update-ref", "-d", "refs/heads/master"],
        check=True,
    )

    # ----- bootstrap: launcher + venv (first install from src-repo) -----
    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)  # must resolve ai_hats from the project venv only

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    # ----- convert to editable so the package dir carries .git for the probe -----
    venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert venv_python.is_file(), f"project venv python missing at {venv_python}"
    subprocess.run(
        ["uv", "pip", "uninstall", "--python", str(venv_python), "ai-hats"],
        env=env,
        check=True,
        timeout=60,
    )
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), "-e", str(src_repo)],
        env=env,
        check=True,
        timeout=120,
    )
    shutil.rmtree(project / ".agent" / "ai-hats" / "versions", ignore_errors=True)
    where = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import ai_hats, pathlib; print(pathlib.Path(ai_hats.__file__).resolve())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()
    assert str(src_repo) in where, (
        f"editable conversion did not take effect: ai_hats.__file__={where!r}"
    )

    # ----- swap probe target to the trunk-default fake remote (git+ form) -----
    # git+ prefix exercises the bare-URL coercion (_coerce_to_https) before
    # `git ls-remote`, which rejects a git+ argument outright.
    env[ENV_REPO_URL] = f"git+file://{fake_remote}"

    # ----- assertion: guard fires against trunk HEAD, exit 3 -----
    refuse = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project,
        env=env,
        timeout=60,
        expect_exit=3,
    )
    combined = refuse.stdout + refuse.stderr
    assert "Refusing to downgrade" in combined, (
        f"guard did not fire against the custom edge repo's default branch; "
        f"combined output:\n{combined}"
    )
    assert "edge remote" in combined, (
        f"refusal should reference the edge remote, not hardwired master; "
        f"combined output:\n{combined}"
    )
