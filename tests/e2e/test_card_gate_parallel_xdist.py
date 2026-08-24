"""e2e (HATS-1812)

flow:   an agent runs either card quality gate through the materialized Bash hook chain
cmds:
    ai-hats self init -p claude -r maintainer --no-wizard
    make done-gate
    make merge-gate
expect: the whole PreToolUse chain allows the command and the real gate entry point sends
        adaptive xdist flags to every stage through PYTEST_ADDOPTS
why:    HATS-1812 found that gate stage names were shared while execution flags depended
        on the entry point, making the same unit stage take 104s from a card and 27s pre-push
"""  # comment-length: allow — e2e catalog flow schema

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from _helpers.git import git, init_repo
from _helpers.hook_chain import build_session_settings, run_chain, run_unasked

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def maintainer_project(shared_launcher, tmp_path: Path, monkeypatch):
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTEST_ADDOPTS", None)
    env["HOME"] = str(tmp_path / "home")
    env["PYTHON"] = sys.executable
    library_root = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
    env["AI_HATS_LIBRARY_ROOT"] = str(library_root)
    monkeypatch.setenv("AI_HATS_LIBRARY_ROOT", str(library_root))
    Path(env["HOME"]).mkdir()

    project = tmp_path / "project"
    project.mkdir()
    init_repo(project, branch="master", harden=True)

    initialized = subprocess.run(  # noqa: S603 - launcher from the installed e2e fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "maintainer", "--no-wizard"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert initialized.returncode == 0, initialized.stdout + initialized.stderr

    shutil.copy2(REPO_ROOT / "Makefile", project / "Makefile")
    scripts = project / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "ci-local.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == "--stages" ]]; then echo "probe"; exit 0; fi\n'
        'printf "%s" "${PYTEST_ADDOPTS:-}" > "${AI_HATS_TEST_CAPTURE:?}"\n',
        encoding="utf-8",
    )
    git(project, "add", "-A")
    git(project, "commit", "-m", "seed gate project")

    settings = build_session_settings(project, role="maintainer", session_id="sid-card-gate")
    return project, env, settings


@pytest.mark.parametrize("gate", ("done-gate", "merge-gate"))
def test_card_gate_uses_adaptive_xdist_after_the_composed_chain(maintainer_project, gate):
    """HATS-1812: both card entry points apply one shared gate policy."""
    project, env, settings = maintainer_project
    capture = project.parent / f"{gate}.addopts"
    command = f"make {gate}"

    verdict = run_chain(project, command, settings=settings, env=env)
    assert verdict.decision == "allow", f"the composed chain blocked `{command}`: {verdict}"

    ran = run_unasked(
        project,
        command,
        env={**env, "AI_HATS_TEST_CAPTURE": str(capture)},
    )
    assert ran.returncode == 0, ran.output

    addopts = shlex.split(capture.read_text(encoding="utf-8"))
    assert "--tb=line" in addopts
    assert "--no-header" in addopts
    assert "-p" in addopts and "no:cacheprovider" in addopts
    assert "--dist=loadgroup" in addopts
    worker_flag = next((arg for arg in addopts if arg.startswith("-n") and arg[2:].isdigit()), "")
    assert worker_flag, addopts
    assert 1 <= int(worker_flag[2:]) <= 8
