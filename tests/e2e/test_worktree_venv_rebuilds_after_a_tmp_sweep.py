"""e2e (HATS-1339)

flow:   a task worktree sits under $TMPDIR long enough for the OS sweeper to
        empty its venv, and the next worktree create runs the provisioning hook
cmds:
    bash provision-venv.sh        # the real wt_in hook, real uv, real venv
expect: the hook rebuilds a venv whose files the sweeper deleted instead of
        reading the surviving bin/python as "already provisioned"
why:    a gutted venv failed the done-gate with a ModuleNotFoundError naming an
        unrelated module, and re-running the hook could not heal it
"""

# Real ``uv`` against a real venv on disk: the defect is that an executable
# ``bin/python`` outlives the files it needs, which only a filesystem can stage.
# Found while running the HATS-1339 done-gate, which died on a swept worktree
# venv with a ModuleNotFoundError naming a module nobody had touched.

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/ai-hats-dev/skills"
    / "worktree-venv/hooks/provision-venv.sh"
)

#: A project small enough that a real editable install is a couple of seconds.
_PYPROJECT = """\
[project]
name = "wtprobe"
version = "0.1.0"

[project.optional-dependencies]
dev = []

[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["wtprobe"]
"""


def _records(venv: Path) -> list[Path]:
    """Every installed distribution's RECORD — the proof that files survived."""
    return sorted(venv.glob("lib/python*/site-packages/*.dist-info/RECORD"))


def _run_hook(worktree: Path, project_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["AI_HATS_WORKTREE_PATH"] = str(worktree)
    env["AI_HATS_PROJECT_DIR"] = str(project_dir)
    env["AI_HATS_EVENT"] = "wt_in"
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(project_dir),  # the runner invokes hooks from MAIN, never the worktree
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _gut_site_packages(venv: Path) -> None:
    """What the OS tmp sweeper leaves: files gone, directory skeleton intact.

    ``bin/python`` is deliberately spared — it is a symlink to an interpreter
    living outside $TMPDIR, so it survives the sweep and is exactly what made the
    old ``-x`` probe answer "already provisioned".
    """
    for site_packages in venv.glob("lib/python*/site-packages"):
        for path in site_packages.rglob("*"):
            if path.is_file() or path.is_symlink():
                path.unlink()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is what provisions the venv")
def test_the_hook_rebuilds_a_venv_the_tmp_sweeper_gutted(tmp_path: Path) -> None:
    worktree = tmp_path / "wt"
    (worktree / "wtprobe").mkdir(parents=True)
    (worktree / "wtprobe" / "__init__.py").write_text("VALUE = 1\n")
    (worktree / "pyproject.toml").write_text(_PYPROJECT)
    project_dir = tmp_path / "main"
    project_dir.mkdir()
    venv = worktree / ".venv"

    first = _run_hook(worktree, project_dir)
    assert first.returncode == 0, first.stdout + first.stderr
    assert _records(venv), f"nothing was installed: {first.stdout}{first.stderr}"

    _gut_site_packages(venv)
    assert not _records(venv), "the sweep left the files it was meant to delete"
    assert os.access(venv / "bin" / "python", os.X_OK), (
        "bin/python must survive — it is what the old probe mistook for a venv"
    )

    second = _run_hook(worktree, project_dir)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "unusable" in second.stdout, second.stdout
    assert _records(venv), "the hook skipped a gutted venv instead of rebuilding it"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is what provisions the venv")
def test_a_healthy_venv_is_left_alone(tmp_path: Path) -> None:
    """The control: without it the fix above could just be "always rebuild"."""
    worktree = tmp_path / "wt"
    (worktree / "wtprobe").mkdir(parents=True)
    (worktree / "wtprobe" / "__init__.py").write_text("VALUE = 1\n")
    (worktree / "pyproject.toml").write_text(_PYPROJECT)
    project_dir = tmp_path / "main"
    project_dir.mkdir()

    assert _run_hook(worktree, project_dir).returncode == 0
    marker = worktree / ".venv" / "ai-hats-e2e-marker"
    marker.write_text("survives an idempotent re-run\n")

    second = _run_hook(worktree, project_dir)

    assert second.returncode == 0, second.stdout + second.stderr
    assert "already usable" in second.stdout, second.stdout
    assert marker.exists(), "a usable venv was rebuilt anyway"
