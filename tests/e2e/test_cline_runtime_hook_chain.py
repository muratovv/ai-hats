"""e2e (HATS-1775)

flow:   a real Cline HITL launch invokes its materialized PreToolUse chain
cmds:
    git push --force origin master
expect: the composed safety chain cancels the unapproved tool call through Cline's
        native hook protocol and leaves the project root clean
why:    role composition is not protection unless the Cline process actually runs it
        before each tool call
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from _helpers.hook_chain import install_cline_surface_venv, run_cline_hook_session
from _helpers.repo_src import build_src

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _snapshot_non_agent_files(project: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if relative.parts[0] == ".agent" or not path.is_file():
            continue
        snapshot[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def test_cline_hitl_runs_composed_pretooluse_chain(tmp_path: Path, shared_launcher) -> None:
    launcher, base_env, _venv = shared_launcher
    project = tmp_path / "project"
    project.mkdir()
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    for key in [name for name in env if name.startswith("AI_HATS_") and name.endswith("ACK")]:
        del env[key]
    env["HOME"] = str(tmp_path / "home")
    env["AI_HATS_CACHE_HOME"] = str(tmp_path / "cache")
    env["AI_HATS_NO_UPDATE_CHECK"] = "1"
    surface_venv = install_cline_surface_venv(build_src(REPO_ROOT), tmp_path / "cline-venv", env)
    env["AI_HATS_VENV"] = str(surface_venv)

    initialized = subprocess.run(  # noqa: S603 - fixture provides the installed launcher
        [str(launcher), "self", "init", "-p", "cline", "-r", "maintainer", "--no-wizard"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr
    before = _snapshot_non_agent_files(project)

    launched, capture = run_cline_hook_session(
        launcher,
        project,
        env,
        tmp_path / "driver",
        command="git push --force origin master",
    )

    assert launched.returncode == 0, launched.stdout + launched.stderr
    assert "--hooks-dir" in capture["argv"]
    assert capture["hook_returncode"] == 0, capture["hook_stderr"]
    assert capture["manifest_session_id"] == capture["session_id"]
    assert any("safety-guard" in tag for tag in capture["pretooluse_tags"])
    assert capture["hook_output"]["cancel"] is True
    assert "cannot ask for permission" in capture["hook_output"]["errorMessage"]

    safe_launched, safe_capture = run_cline_hook_session(
        launcher,
        project,
        env,
        tmp_path / "safe-driver",
        command="echo safe",
    )

    assert safe_launched.returncode == 0, safe_launched.stdout + safe_launched.stderr
    assert safe_capture["hook_returncode"] == 0, safe_capture["hook_stderr"]
    assert safe_capture["hook_output"] == {"cancel": False}
    assert _snapshot_non_agent_files(project) == before
    assert not (project / ".cline").exists()
