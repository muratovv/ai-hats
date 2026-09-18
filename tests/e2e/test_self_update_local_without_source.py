"""e2e (HATS-1998)

flow: a project whose ai-hats.yaml says `channel: local` with no `path`, in a
      directory that is not a Python project — the wizard's `local` on a host
      whose ai-hats was a git install
cmds:
    ai-hats self update
    ai-hats self update --check
expect: self update installs edge for that run, names the config fix, and leaves
        the yaml alone; --check exits 1 with a BROKEN harness row; with a
        path set the same commands reinstall editable and report OK
why: without this heal, self update ran `uv pip install -e <project root>` and
     handed the user uv's refusal with every triage row green"""
# comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from _helpers.project import pin_edge_channel
from _helpers.repo_src import build_src

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

pytestmark = [
    pytest.mark.install_heavy,
    pytest.mark.install,
]  # real uv installs at call time → capped via conftest

FIX = "ai-hats config set --channel local --path"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if expect_exit is not None and result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _bootstrap(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Install the launcher, bootstrap the project venv from the local source
    (edge), init a role. Returns ``(launcher, project, env)``."""
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    user_home = tmp_path / "userhome"
    launcher.parent.mkdir(parents=True)
    project.mkdir()
    user_home.mkdir()
    pin_edge_channel(project)

    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("AI_HATS_")
        and k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH")
    }
    env["AI_HATS_USER_HOME"] = str(user_home)
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env["AI_HATS_BUMP_BACKUP_DIR"] = str(tmp_path / "backups")
    env["COLUMNS"] = "400"  # rich folds a long path at 80 columns when not a tty

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher), "self", "update", "--force-downgrade"], cwd=project, env=env, timeout=300)
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project,
        env=env,
        timeout=60,
    )
    return launcher, project, env


def _set_harness(project: Path, block: str) -> None:
    """Replace the yaml's ``harness:`` block (and only it) with ``block``."""
    cfg = project / PROJECT_CONFIG
    kept: list[str] = []
    in_block = False
    for line in cfg.read_text().splitlines():
        if line.startswith("harness:"):
            in_block = True
            continue
        if in_block and line.startswith(" "):
            continue
        in_block = False
        kept.append(line)
    cfg.write_text("\n".join(kept).rstrip("\n") + "\n" + block)


@pytest.mark.integration
def test_local_channel_without_a_source_is_healed_and_named(tmp_path: Path) -> None:
    launcher, project, env = _bootstrap(tmp_path)
    assert not (project / "pyproject.toml").exists(), "premise: the root is not a Python project"
    _set_harness(project, "harness:\n  channel: local\n")
    before = (project / PROJECT_CONFIG).read_text()

    res = _run(
        [str(launcher), "self", "update", "--force-downgrade"],
        cwd=project,
        env=env,
        timeout=300,
    )
    combined = res.stdout + res.stderr
    assert "not an installable project" in combined, combined
    assert "installing edge" in combined, combined
    assert FIX in combined, combined
    assert "uv pip install -e" not in combined, f"an uninstallable source was -e'd:\n{combined}"
    assert (project / PROJECT_CONFIG).read_text() == before, "the yaml is the user's — untouched"

    check = _run(
        [str(launcher), "self", "update", "--check"],
        cwd=project,
        env=env,
        timeout=120,
        expect_exit=1,
    )
    flat = " ".join(check.stdout.split())
    assert "BROKEN harness" in flat, f"expected a BROKEN harness row:\n{check.stdout}"
    assert FIX in flat, check.stdout

    # The control: the same commands with the source set reinstall editable
    # from it and report the row OK.
    _set_harness(project, f"harness:\n  channel: local\n  path: {env[ENV_REPO_URL]}\n")
    res_ok = _run(
        [str(launcher), "self", "update"],
        cwd=project,
        env=env,
        timeout=300,
    )
    assert f"uv pip install -e {env[ENV_REPO_URL]}" in res_ok.stdout + res_ok.stderr, (
        f"expected the editable reinstall of the configured path:\n{res_ok.stdout}\n{res_ok.stderr}"
    )
    check_ok = _run([str(launcher), "self", "update", "--check"], cwd=project, env=env, timeout=120)
    flat_ok = " ".join(check_ok.stdout.split())
    assert f"OK harness local → {env[ENV_REPO_URL]}" in flat_ok, (
        f"expected an OK harness row naming the source:\n{check_ok.stdout}"
    )
