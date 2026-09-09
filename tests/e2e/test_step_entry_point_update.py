"""e2e (HATS-1810)

flow:   an editable install's live `ai_hats.steps` declarations drift from its
        installed metadata before a normal command and before `self update`
cmds:
    ai-hats --help
    ai-hats self update
expect: each stale snapshot is repaired before resolution; a reinstall with a
        broken first-party step target fails post-install verification
why:    `uv sync --check` accepts stale editable entry-point metadata, while the
        runtime resolver sees only the installed snapshot
"""  # comment-length: allow — the e2e catalog header format

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from ai_hats.constants import ENV_LAUNCHER_DEST, ENV_REPO_URL
from ai_hats.paths import ENV_AI_HATS_VENV, PROJECT_CONFIG

from _helpers.env import clean_env  # noqa: E402

pytestmark = [pytest.mark.install_heavy, pytest.mark.install]

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_LAUNCHER = REPO_ROOT / "scripts" / "install-launcher.sh"
STEP_DECLARATION = 'check_update_async = "ai_hats.pipeline.steps.check_update:CheckUpdateAsync"'
BROKEN_STEP_DECLARATION = 'check_update_async = "ai_hats.pipeline.steps.check_update:MissingStep"'
RESOLVE_STEP = 'from ai_hats.pipeline.registry import get; get("check_update_async")'


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: int, expect: int = 0):
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == expect, (
        f"{cmd} expected exit {expect}, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result


def _without_step_declaration(pyproject: str) -> str:
    """Build the pre-HATS-1810 metadata snapshot that exposes the drift."""
    assert pyproject.count(STEP_DECLARATION) == 1
    return pyproject.replace(f"{STEP_DECLARATION}\n", "", 1)


def _with_broken_step_target(pyproject: str) -> str:
    assert pyproject.count(STEP_DECLARATION) == 1
    return pyproject.replace(STEP_DECLARATION, BROKEN_STEP_DECLARATION, 1)


def _install_editable(src_repo: Path, python: Path, env: dict[str, str]) -> None:
    _run(
        [
            "uv",
            "pip",
            "install",
            "--reinstall",
            "--python",
            str(python),
            "-e",
            str(src_repo),
        ],
        cwd=src_repo,
        env=env,
        timeout=180,
    )


@pytest.mark.integration
def test_editable_step_metadata_repairs_and_verifies_across_update_paths(tmp_path: Path) -> None:
    src_repo = tmp_path / "src-repo"
    launcher_dest = tmp_path / "bin" / "ai-hats"
    project = tmp_path / "project"
    launcher_dest.parent.mkdir(parents=True)
    project.mkdir()

    _run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(src_repo)],
        cwd=tmp_path,
        env=clean_env(),
        timeout=60,
    )
    pyproject = src_repo / "pyproject.toml"
    current_pyproject = pyproject.read_text()
    pyproject.write_text(_without_step_declaration(current_pyproject))

    (project / PROJECT_CONFIG).write_text(
        "schema_version: 4\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "provider: claude\n"
        "harness:\n"
        "  channel: local\n"
        f"  path: {src_repo}\n"
    )
    env = clean_env(os.environ)
    env[ENV_LAUNCHER_DEST] = str(launcher_dest)
    env[ENV_REPO_URL] = str(src_repo)
    env.pop(ENV_AI_HATS_VENV, None)

    _run(["bash", str(INSTALL_LAUNCHER)], cwd=tmp_path, env=env, timeout=60)
    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)

    python = project / ".agent" / "ai-hats" / ".venv" / "bin" / "python"
    assert python.is_file()

    pyproject.write_text(current_pyproject)
    _run([str(python), "-c", RESOLVE_STEP], cwd=project, env=env, timeout=60, expect=1)

    healed = _run([str(launcher_dest), "--help"], cwd=project, env=env, timeout=180)
    assert "ai_hats.steps metadata is stale" in f"{healed.stdout}\n{healed.stderr}"
    _run([str(python), "-c", RESOLVE_STEP], cwd=project, env=env, timeout=60)

    pyproject.write_text(_without_step_declaration(current_pyproject))
    _install_editable(src_repo, python, env)
    pyproject.write_text(current_pyproject)
    _run([str(python), "-c", RESOLVE_STEP], cwd=project, env=env, timeout=60, expect=1)

    _run([str(launcher_dest), "self", "update"], cwd=project, env=env, timeout=300)
    _run([str(python), "-c", RESOLVE_STEP], cwd=project, env=env, timeout=60)

    pyproject.write_text(_with_broken_step_target(current_pyproject))
    _install_editable(src_repo, python, env)
    rejected = _run(
        [str(launcher_dest), "self", "update"],
        cwd=project,
        env=env,
        timeout=300,
        expect=1,
    )
    output = f"{rejected.stdout}\n{rejected.stderr}"
    assert "Post-install verify failed" in output
    assert "check_update_async" in output
