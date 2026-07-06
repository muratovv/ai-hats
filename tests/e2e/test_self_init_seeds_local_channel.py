"""E2E (HATS-938): `self init` on an editable host auto-seeds harness.channel:local.

Value under test: a fresh project initialised on a machine whose ai-hats install
resolves to a LOCAL editable source must write ``harness: {channel: local, path:
<src>}`` with NO manual yaml edit — so its first heal/`self update` installs
editable-from-local instead of the (currently broken) remote release.

Signal path exercised end-to-end: no ``AI_HATS_VENV``, no pre-existing yaml, and
``AI_HATS_REPO_URL`` pointing at a real local checkout. The launcher's
``detect_init_src`` sees a local install source and exports ``AI_HATS_INIT_SRC``
BEFORE the venv bootstrap; the python ``self init`` then seeds channel:local from
that env. ``-r/-p`` + non-TTY stdin takes the no-wizard path so init runs no extra
pip install — the seed is the only observable write.

Fail-under-revert: revert either the launcher export OR the ``Assembler.init``
seed and the fresh venv is a NON-editable remote-style install with no editable
signal → init keeps the STABLE default → no ``harness`` block → assertion fails.

Setup contract (real subprocess + real uv + real launcher), per
``dev_rule_e2e_gate``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from ai_hats.constants import ENV_AI_HATS_INIT_SRC, ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

pytestmark = pytest.mark.install_heavy  # real uv install at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,  # non-TTY → self init takes the no-wizard path
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_self_init_seeds_local_channel_on_editable_host(tmp_path: Path) -> None:
    """Fresh project + local install source → auto-seeded harness.channel:local."""
    import os

    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    # A real local checkout is both the install source (PIP_TARGET) and the
    # expected seeded path — offline, deterministic, no GitHub pull.
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)  # local dir → launcher exports AI_HATS_INIT_SRC
    env.pop(ENV_AI_HATS_VENV, None)  # force per-project default venv (no host pin)
    env.pop(ENV_AI_HATS_INIT_SRC, None)  # launcher must compute it, not inherit it
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    # No prior `self update`; `self init` is the FIRST command. Greenfield (no
    # ai-hats.yaml) so the auto-seed is exercised, not a re-init no-op.
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=300,
    )

    cfg = project / PROJECT_CONFIG
    assert cfg.exists(), "self init did not write ai-hats.yaml"
    raw = yaml.safe_load(cfg.read_text())
    assert raw.get("harness") == {"channel": "local", "path": str(src_repo)}, (
        f"expected auto-seeded channel:local path={src_repo}, got {raw.get('harness')!r}"
    )
