"""Unit tests for HATS-1291: which pytest the smoke gate spawns.

The hook must prefer the checkout being committed (``<git-toplevel>/.venv``)
over PATH — in a worktree PATH's pytest is MAIN's, and its editable install
makes the e2e tier test the wrong source.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOOK_PATH = (
    Path(__file__).parent.parent
    / "packages"
    / "ai-hats-library"
    / "src"
    / "ai_hats_library"
    / "core"
    / "skills"
    / "git-mastery"
    / "git_hooks"
    / "pre-commit-smoke.sh"
)

# A stub pytest that records the fact it ran, then reports "no tests collected"
# (exit 5) — the hook treats 5 as a silent pass, so the stub never needs tests.
_STUB = """#!/usr/bin/env bash
echo "{tag}" >> "{marker}"
exit 5
"""


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo whose backlog has an executing `integration` task (gate armed)."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")

    task_dir = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks" / "T-1"
    task_dir.mkdir(parents=True)
    (task_dir / "task.yaml").write_text("state: execute\ntags:\n  - integration\n")

    marker = tmp_path / "which-pytest.txt"
    return project, marker


def _install_stub(at: Path, tag: str, marker: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.write_text(_STUB.format(tag=tag, marker=marker))
    at.chmod(0o755)


def _run_hook(cwd: Path, path_dir: Path) -> subprocess.CompletedProcess:
    """Run the hook with ``path_dir`` as the ONLY source of a PATH pytest."""
    env = os.environ.copy()
    env.pop("AI_HATS_SMOKE_SKIP", None)
    env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin"
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def test_smoke_hook_prefers_repo_local_venv_over_path(tmp_path: Path) -> None:
    """A repo-local .venv/bin/pytest wins over whatever PATH offers."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "pytest", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "pytest", "VENV", marker)

    result = _run_hook(project, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["VENV"], (
        f"expected the repo-local venv pytest to run, got {marker.read_text()!r}"
    )


def test_smoke_hook_falls_back_to_path_without_a_repo_local_venv(tmp_path: Path) -> None:
    """No .venv in the checkout — the pre-HATS-1291 PATH lookup still applies."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "pytest", "PATH", marker)

    result = _run_hook(project, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["PATH"]


def test_smoke_hook_in_a_worktree_prefers_the_worktrees_own_venv(tmp_path: Path) -> None:
    """The regression this card exists for (HATS-1291).

    A worktree's own venv must beat MAIN's. The hook resolves the backlog via
    --git-common-dir, which points at MAIN from inside a worktree; using that
    root for the interpreter too is exactly what made the first commit fail.
    """
    project, marker = _make_project(tmp_path)
    (project / "seed.txt").write_text("x")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "seed")

    worktree = tmp_path / "wt"
    _git(project, "worktree", "add", "-q", "-b", "task/x", str(worktree))

    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "pytest", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "pytest", "MAIN", marker)
    _install_stub(worktree / ".venv" / "bin" / "pytest", "WORKTREE", marker)

    result = _run_hook(worktree, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["WORKTREE"], (
        f"expected the worktree's own venv pytest, got {marker.read_text()!r}"
    )
