"""Unit tests for HATS-1291: which pytest the smoke gate spawns.

The hook must prefer the checkout being committed (``<git-toplevel>/.venv``)
over PATH — in a worktree PATH's pytest is MAIN's, and its editable install
makes the e2e tier test the wrong source.

HATS-1355: every case runs under each distinct bash on the host, so macOS's
``/bin/bash`` 3.2 is exercised and not just the modern one on PATH. Both faults
that have shipped here were 4.x-only and invisible under bash 5.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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
echo "$@" >> "{marker}.args"
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


def _bash_interpreters() -> list[tuple[str, str]]:
    """Every distinct bash on the host, as ``(test_id, path)``.

    macOS keeps 3.2 at /bin/bash while PATH usually resolves a homebrew 5.x —
    running only the latter is what let two 4.x-only constructs ship.
    """
    found: dict[str, tuple[str, str]] = {}
    for candidate in (shutil.which("bash"), "/bin/bash"):
        if not candidate or not Path(candidate).exists():
            continue
        real = str(Path(candidate).resolve())
        if real in found:
            continue
        probe = subprocess.run(
            [candidate, "-c", 'echo "$BASH_VERSION"'], capture_output=True, text=True, check=False
        )
        version = probe.stdout.strip() or "unknown"
        found[real] = (f"bash-{version.split('(')[0]}", candidate)
    return list(found.values())


@pytest.fixture(params=_bash_interpreters(), ids=lambda pair: pair[0])
def bash_bin(request: pytest.FixtureRequest) -> str:
    return request.param[1]


def _run_hook(cwd: Path, path_dir: Path, bash_bin: str = "bash") -> subprocess.CompletedProcess:
    """Run the hook with ``path_dir`` as the ONLY source of a PATH pytest."""
    env = os.environ.copy()
    env.pop("AI_HATS_SMOKE_SKIP", None)
    env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin"
    return subprocess.run(
        [bash_bin, str(HOOK_PATH)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def test_smoke_hook_prefers_repo_local_venv_over_path(tmp_path: Path, bash_bin: str) -> None:
    """A repo-local .venv/bin/pytest wins over whatever PATH offers."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "pytest", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "pytest", "VENV", marker)

    result = _run_hook(project, path_dir, bash_bin)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["VENV"], (
        f"expected the repo-local venv pytest to run, got {marker.read_text()!r}"
    )


def test_smoke_hook_falls_back_to_path_without_a_repo_local_venv(
    tmp_path: Path, bash_bin: str
) -> None:
    """No .venv in the checkout — the pre-HATS-1291 PATH lookup still applies."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "pytest", "PATH", marker)

    result = _run_hook(project, path_dir, bash_bin)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["PATH"]


def test_smoke_hook_in_a_worktree_prefers_the_worktrees_own_venv(
    tmp_path: Path, bash_bin: str
) -> None:
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

    result = _run_hook(worktree, path_dir, bash_bin)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["WORKTREE"], (
        f"expected the worktree's own venv pytest, got {marker.read_text()!r}"
    )


def test_smoke_hook_passes_e2e_target_path(tmp_path: Path, bash_bin: str) -> None:
    """HATS-1345: the smoke hook targets tests/e2e/ so collection errors elsewhere don't block commits."""
    project, marker = _make_project(tmp_path)
    (project / "tests" / "e2e").mkdir(parents=True)
    path_dir = tmp_path / "pathbin"
    _install_stub(project / ".venv" / "bin" / "pytest", "VENV", marker)

    result = _run_hook(project, path_dir, bash_bin)

    assert result.returncode == 0, result.stderr
    args_file = Path(f"{marker}.args")
    assert args_file.is_file()
    assert "tests/e2e/" in args_file.read_text().split()


def test_smoke_hook_omits_the_target_path_when_absent(tmp_path: Path, bash_bin: str) -> None:
    """HATS-1352: no tests/e2e/ => no path argument, so pytest falls back to testpaths.

    Passing a path that does not exist makes pytest answer rc=4 (usage error),
    which the hook does not treat as "nothing to run" — so a consumer project
    without the directory had every commit blocked.
    """
    project, marker = _make_project(tmp_path)
    assert not (project / "tests" / "e2e").exists()
    path_dir = tmp_path / "pathbin"
    _install_stub(project / ".venv" / "bin" / "pytest", "VENV", marker)

    result = _run_hook(project, path_dir, bash_bin)

    assert result.returncode == 0, result.stderr
    args_file = Path(f"{marker}.args")
    assert args_file.is_file(), "the hook never invoked pytest"
    assert "tests/e2e/" not in args_file.read_text().split(), (
        f"a non-existent path still reached pytest: {args_file.read_text()!r}"
    )
