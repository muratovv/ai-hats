"""E2E: fall-through probe refuses a venv missing ANY workspace member (HATS-895).

Discriminates against the partial fix ``import ai_hats.cli`` alone:
``ai_hats_wt`` is NOT in the CLI import chain (deferred per ADR-0013), so a
wt-mined venv would pass a cli-only probe and exit 0 — this test loops over
EVERY ``packages/*`` member and demands a clean refusal + heal hint for each.

Fail-under-revert: bare probe → core-mine execs a raw ``ModuleNotFoundError``
traceback (no "not importable" message), wt-mine exits 0. Real subprocess +
real uv + real launcher per ``dev_rule_e2e_gate``.
"""
# comment-length: allow — deliberate fail-under-revert contract docstring

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.e2e._helpers.workspace import workspace_members
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG
from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL

pytestmark = pytest.mark.install_heavy  # real uv installs at call time → capped via conftest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"


def _run(cmd, *, cwd, env, timeout, expect_exit=0):
    result = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != expect_exit:
        raise AssertionError(
            f"{cmd} expected exit {expect_exit}, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_e2e_regular_call_refused_for_each_missing_member(tmp_path: Path) -> None:
    """For every packages/* member: mine it → `ai-hats --help` refuses with hint."""
    src_repo = tmp_path / "src-repo"
    launcher = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher.parent.mkdir(parents=True)
    project.mkdir()

    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)], check=True)
    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )

    env = os.environ.copy()
    env[ENV_LAUNCHER_DEST] = str(launcher)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)
    env.pop("PYTHONPATH", None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run(
        [str(launcher), "self", "init", "-r", "assistant", "-p", "claude"],
        cwd=project, env=env, timeout=300,
    )

    py = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    members = workspace_members(src_repo)
    assert members, "workspace has no packages/* members — test premise broken"

    for dist, imp in members:
        _run(
            ["uv", "pip", "uninstall", "--python", str(py), dist],
            cwd=tmp_path, env=env, timeout=60,
        )
        bare = subprocess.run([str(py), "-c", "import ai_hats"], capture_output=True, text=True)
        assert bare.returncode == 0, (
            f"premise broken for {dist}: bare import must pass on the mined venv\n{bare.stderr}"
        )

        result = subprocess.run(
            [str(launcher), "--help"], cwd=str(project), env=env,
            capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 1, (
            f"mined {dist} ({imp}): expected clean refusal (exit 1), got "
            f"{result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "not importable" in result.stderr, (
            f"mined {dist}: expected 'not importable' refusal, got:\n{result.stderr}"
        )
        assert "self update" in result.stderr, (
            f"mined {dist}: expected heal hint, got:\n{result.stderr}"
        )

        # Restore via the root editable install (the PoC-2 contract: uv re-adds
        # the missing member), then sanity-check before the next round.
        _run(
            ["uv", "pip", "install", "--quiet", "--python", str(py), "-e", str(src_repo)],
            cwd=tmp_path, env=env, timeout=180,
        )
        _run([str(launcher), "--help"], cwd=project, env=env, timeout=120)
