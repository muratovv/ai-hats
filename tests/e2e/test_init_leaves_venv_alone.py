"""E2E (HATS-1215): flag-only `self init` configures the project and leaves the venv alone.

This file used to assert the opposite — that `self init` reconciled the venv to an
editable channel:local install (HATS-1125). Commit `5c15951a` deliberately removed
that implicit `_run_self_update()` from `init`, per HATS-1215's requirement that
`self init` be "pure local & offline… without network side-effects or package
upgrades". The assertion outlived its subject and went red on the e2e gate
(HATS-1250); this is its replacement, pinning the contract that is now true.

Sibling coverage: `test_init_wizard_reinit.py` pins the same no-implicit-update
contract on the PTY/wizard path. `5c15951a` removed **two** call sites; this file
covers the other one, the non-wizard flag-only path.

The assertion is deliberately *not* "the install is non-editable" — that would pin an
incidental artefact of how the launcher happened to install ai-hats, and would break on
an unrelated packaging change. What matters is that a second `init` does not mutate the
venv, which stays true however the venv was provisioned.

Per `dev_rule_e2e_gate`: real subprocess + real uv + real launcher.

Deliberate long contract module docstring — noqa: comment-length.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.install_heavy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

# Identity of the installed ai-hats distribution: url + editability. If `init` ever
# reinstalls the package, this is what changes.
PROBE_CODE = (
    "from importlib.metadata import distribution\n"
    'print(distribution("ai-hats").read_text("direct_url.json") or "")\n'
)


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY → self init takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_flag_only_reinit_does_not_touch_the_venv(tmp_path: Path) -> None:
    """A second flag-only `self init` must not reinstall or re-point the venv."""
    from _helpers.env import clean_env

    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    subprocess.run(["git", "-C", str(src_repo), "config", "user.email", "e2e@test"], check=True)
    subprocess.run(["git", "-C", str(src_repo), "config", "user.name", "E2E"], check=True)

    env = clean_env()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env["AI_HATS_ALLOW_SELF_UPDATE_IN_TEST"] = "1"
    env.pop(ENV_AI_HATS_VENV, None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)

    init_cmd = [
        str(launcher_dest),
        "self",
        "init",
        "-r",
        "assistant",
        "-p",
        "claude",
        "--channel",
        "local",
        "--harness-path",
        str(src_repo),
    ]

    # First init bootstraps the project venv — that is the launcher's job, not init's.
    _run(init_cmd, cwd=project, env=env, timeout=300)

    cfg = project / PROJECT_CONFIG
    assert cfg.exists(), "self init did not write ai-hats.yaml"
    assert yaml.safe_load(cfg.read_text()).get("harness", {}).get("channel") == "local"

    proj_venv_python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert proj_venv_python.exists(), "project venv interpreter missing"

    def venv_identity() -> str:
        res = _run([str(proj_venv_python), "-c", PROBE_CODE], cwd=project, env=env, timeout=30)
        return res.stdout.strip()

    before = venv_identity()
    # Guards against a vacuous pass: comparing "" to "" would succeed while proving
    # nothing about whether init left the install alone.
    assert before, "probe read no direct_url.json — the comparison below would be vacuous"

    _run(init_cmd, cwd=project, env=env, timeout=300)
    after = venv_identity()

    assert after == before, (
        "self init must not reinstall or re-point the venv (HATS-1215: pure local, no "
        f"package upgrades).\n  before: {before}\n  after : {after}"
    )
