"""E2E: ``ai-hats self update`` survives an ai-hats.yaml the installed code
cannot fully validate (HATS-581).

The forward-compat deadlock this anchors (reproduced live on a proxmox
project): an OLDER installed binary chokes on a field a NEWER binary wrote
into ai-hats.yaml (``migration_step``, added without a schema_version
bump). ``ProjectConfig`` was ``extra="forbid"`` → hard crash at
``_assembler(project_dir)`` BEFORE the package install — so ``self update``,
the exact recovery command, was blocked by the thing it would have fixed.

Two layers under test:
  * Fix #2 — unknown keys are stripped with a WARN instead of crashing.
  * Fix #1 — even a non-strippable error (wrong-type value) degrades the
    update instead of dumping a traceback.

Per ``dev_rule_e2e_gate``: real ``bash`` + real ``pip install`` + real
``ai-hats`` binary, marked ``@pytest.mark.integration``. Each test builds
its own launcher venv because ``self update`` mutates the venv (the
session-shared fixture is read-only by contract).
"""

from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

import pytest

# HATS-589: per-xdist-worker private build source (no-op on serial run).
from _helpers.project import pin_edge_channel
from _helpers.repo_src import build_src

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import PROJECT_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"

pytestmark = pytest.mark.install_heavy  # HATS-678: real uv install at call time → capped via conftest.INSTALL_HEAVY_GROUPS


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


def _bootstrap(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Install the launcher, bootstrap the project venv, init a role.

    Returns ``(launcher_dest, project, env)``. ``env`` carries a per-test
    ``AI_HATS_BUMP_BACKUP_DIR`` so backup tarballs land in a known place.
    """
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    backups = tmp_path / "backups"
    user_home = tmp_path / "userhome"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()
    user_home.mkdir()
    # HATS-764: edge so the bootstrap self update resolves the local source
    # (not the new default-stable PyPI path). Re-pinned after `self init` below.
    pin_edge_channel(project)

    # Isolate from the developer's global config so role resolution is
    # deterministic: scrub ``AI_HATS_*`` / ``VIRTUAL_ENV`` and isolate via
    # ``AI_HATS_USER_HOME`` (HATS-532) NOT ``HOME`` — repointing ``HOME`` empties
    # the warm ``~/.cache/pip`` and can yield a degraded wheel (``ai_hats.library``
    # package-data dropped → "no roles found"). ``PYTHONPATH`` MUST be dropped: a
    # parent ``PYTHONPATH=<repo>/src`` would shadow the inner venv's INSTALLED
    # ``ai_hats`` with the source tree, which has no ``ai_hats.library`` subpackage
    # — so role resolution finds nothing.
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("AI_HATS_")
        and k not in ("VIRTUAL_ENV", "VIRTUAL_ENV_PROMPT", "PYTHONPATH")
    }
    env["AI_HATS_USER_HOME"] = str(user_home)
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(build_src(REPO_ROOT))
    env["AI_HATS_BUMP_BACKUP_DIR"] = str(backups)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=30)
    _run([str(launcher_dest), "self", "update", "--force-downgrade"], cwd=project, env=env, timeout=300)  # HATS-675: 300s = -n8 gate suite norm
    _run(
        [str(launcher_dest), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=60,
    )
    # HATS-764: `self init` rewrote ai-hats.yaml without a harness block (→ the
    # stable default). Re-pin edge so the per-test corrupt-config `self update`s
    # resolve the local source. An unparseable corruption (the garbage-value
    # test) routes through edge anyway via _read_harness's recovery fallback.
    pin_edge_channel(project)
    return launcher_dest, project, env


@pytest.mark.integration
def test_update_survives_unknown_config_key(tmp_path: Path) -> None:
    """Fix #2: an unknown key (proxmox ``migration_step``-style) is stripped
    with a WARN instead of crashing — the update completes and a backup tarball
    exists. (The strip is in-memory per load, like the deprecated-field strip;
    the on-disk key is rewritten on the next config save.)

    Fail-under-revert: with the unknown-key strip reverted, the pre-install read
    raises ``extra_forbidden``; the specific "dropping unknown field" WARN never
    appears (and, with Fix #1 also reverted, the command hard-crashes).
    """
    launcher_dest, project, env = _bootstrap(tmp_path)

    cfg_path = project / PROJECT_CONFIG
    # Deliberately break the schema with a key the binary doesn't know.
    cfg_path.write_text(cfg_path.read_text() + "future_field: 99\n")
    assert "future_field" in cfg_path.read_text()

    res = _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project, env=env, timeout=300,  # HATS-675: 300s = -n8 gate suite norm
    )

    combined = res.stdout + res.stderr
    # Fix #2 signature: the unknown key is stripped with a named WARN, the
    # command does not crash on it.
    assert "dropping unknown field 'future_field'" in combined, (
        f"expected the unknown-field strip WARN.\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    # Recovery net: a pre-bump snapshot must exist.
    tarballs = glob.glob(str(tmp_path / "backups" / "*.tar.gz"))
    assert tarballs, "no bump-backup tarball written during self update"


@pytest.mark.integration
def test_update_survives_garbage_config_value(tmp_path: Path) -> None:
    """Fix #1: a non-strippable error (wrong-type value) degrades the update
    instead of crashing the pre-install config read — the command exits 0 and
    prints a graceful "not parseable" message rather than aborting before the
    package install.

    Note: the fresh-interpreter bump (new code) may still emit its own error
    on the same un-parseable config — that is expected and useful (it tells
    the user what to fix). Fix #1's contract is only that the *recovery
    command* is no longer BLOCKED: the package installs and the top-level
    command does not hard-crash on the pre-install read.

    Fail-under-revert: with the try/except around the pre-install ``_assembler``
    reverted, the wrong-type value raises ``ProjectConfigError`` uncaught BEFORE
    the install → the command exits non-zero with a traceback and never prints
    the degrade message.
    """
    launcher_dest, project, env = _bootstrap(tmp_path)

    cfg_path = project / PROJECT_CONFIG
    # ``manage_gitignore`` is a known bool field; a non-bool value is a
    # validation error the unknown-key strip cannot heal (Fix #1 territory).
    # (We avoid breaking ``schema_version`` itself — a non-int there would
    # raise in the pre-validation migration comparison, a different path.)
    cfg_path.write_text(cfg_path.read_text() + "manage_gitignore: not-a-bool\n")

    res = _run(
        [str(launcher_dest), "self", "update", "--force-downgrade"],
        cwd=project, env=env, timeout=300,  # HATS-675: 300s = -n8 gate suite norm
    )

    assert res.returncode == 0, (
        f"degraded update must still exit 0.\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    combined = res.stdout + res.stderr
    assert "not parseable by the installed version" in combined, (
        f"expected the graceful degrade message.\n"
        f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
