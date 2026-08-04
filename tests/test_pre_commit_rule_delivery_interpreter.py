"""Unit tests for HATS-1314: which python the rule-delivery gate spawns.

Mirror of ``test_pre_commit_smoke_interpreter`` for the second — and, after
HATS-1291, last — pre-commit hook that resolves an interpreter. In a worktree
PATH's ``python3`` is MAIN's, so the checker either runs against the wrong
source or fails its ``import ai_hats`` probe and the gate silently SKIPS.
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
    / "usage"
    / "skills"
    / "rule-delivery-gate"
    / "git_hooks"
    / "pre-commit-rule-delivery.sh"
)

# A stub python that records which copy ran, satisfies the hook's
# `-c "import ai_hats"` probe, and reports a clean check for any other argv.
_STUB = """#!/usr/bin/env bash
if [[ "$1" == "-c" ]]; then exit 0; fi
echo "{tag}" >> "{marker}"
exit 0
"""


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


def _make_project(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with a STAGED library injection — the gate's arming condition."""
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    _git(project, "config", "user.email", "t@t")
    _git(project, "config", "user.name", "t")

    cfg = project / "library" / "traits" / "t" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("injection: see rule x\n")
    _git(project, "add", "-A")

    marker = tmp_path / "which-python.txt"
    return project, marker


def _install_stub(at: Path, tag: str, marker: Path) -> None:
    at.parent.mkdir(parents=True, exist_ok=True)
    at.write_text(_STUB.format(tag=tag, marker=marker))
    at.chmod(0o755)


def _run_hook(cwd: Path, path_dir: Path) -> subprocess.CompletedProcess:
    """Run the hook with ``path_dir`` as the ONLY source of a PATH python3."""
    env = os.environ.copy()
    env.pop("AI_HATS_RULE_DELIVERY_ACK", None)
    env.pop("AI_HATS_RULE_DELIVERY_CMD", None)
    env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin"
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
    )


def test_rule_delivery_prefers_repo_local_venv_over_path(tmp_path: Path) -> None:
    """A repo-local .venv/bin/python3 wins over whatever PATH offers."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "python3", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "python3", "VENV", marker)

    result = _run_hook(project, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["VENV"], (
        f"expected the repo-local venv python, got {marker.read_text()!r}"
    )


def test_rule_delivery_falls_back_to_path_without_a_repo_local_venv(tmp_path: Path) -> None:
    """No .venv in the checkout — the PATH lookup still applies."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "python3", "PATH", marker)

    result = _run_hook(project, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["PATH"]


def test_rule_delivery_in_a_worktree_prefers_the_worktrees_own_venv(tmp_path: Path) -> None:
    """The HATS-1314 regression: MAIN's python must not win inside a worktree.

    Before the fix this gate was the last one resolving purely through PATH, so
    in a worktree it ran MAIN's interpreter — and when that one cannot import
    ``ai_hats`` the gate does not fail, it SKIPS (fail-open), which is worse.
    """
    project, marker = _make_project(tmp_path)
    _git(project, "commit", "-qm", "seed")

    worktree = tmp_path / "wt"
    _git(project, "worktree", "add", "-q", "-b", "task/x", str(worktree))

    cfg = worktree / "library" / "traits" / "t" / "config.yaml"
    cfg.write_text("injection: see rule y\n")
    _git(worktree, "add", "-A")

    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "python3", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "python3", "MAIN", marker)
    _install_stub(worktree / ".venv" / "bin" / "python3", "WORKTREE", marker)

    result = _run_hook(worktree, path_dir)

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["WORKTREE"], (
        f"expected the worktree's own venv python, got {marker.read_text()!r}"
    )


def test_repo_local_venv_without_ai_hats_skips_instead_of_blocking(tmp_path: Path) -> None:
    """HATS-1337 (Z4): the fail-open probe must cover the ABSOLUTE venv python.

    The importability guard was gated on ``[[ "${_cmd[0]}" == python* ]]``, which
    a repo-local ``/…/.venv/bin/python3`` never matches — so the probe was
    skipped, the checker ran anyway, its ``ModuleNotFoundError`` surfaced as a
    non-zero exit, and the gate BLOCKED the commit with "undelivered `see rule
    X` pointer" — a message about a defect that is not there. A missing dev tool
    must never wedge a commit.
    """
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "python3", "PATH", marker)

    # A venv whose interpreter cannot import ai_hats: the `-c` probe fails, and
    # so would the real checker invocation.
    broken = project / ".venv" / "bin" / "python3"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("#!/usr/bin/env bash\nexit 1\n")
    broken.chmod(0o755)

    result = _run_hook(project, path_dir)

    assert result.returncode == 0, (
        "a venv without ai_hats must SKIP the gate (fail-open), not block the "
        f"commit; stderr={result.stderr!r}"
    )
    assert "not importable" in result.stderr, result.stderr
    assert "BLOCKED" not in result.stderr, result.stderr


def test_explicit_cmd_override_still_wins(tmp_path: Path) -> None:
    """AI_HATS_RULE_DELIVERY_CMD pins the interpreter — the venv must not override it."""
    project, marker = _make_project(tmp_path)
    path_dir = tmp_path / "pathbin"
    _install_stub(path_dir / "python3", "PATH", marker)
    _install_stub(project / ".venv" / "bin" / "python3", "VENV", marker)
    _install_stub(tmp_path / "pinned" / "python3", "PINNED", marker)

    env = os.environ.copy()
    env.pop("AI_HATS_RULE_DELIVERY_ACK", None)
    env["PATH"] = f"{path_dir}{os.pathsep}/usr/bin:/bin"
    env["AI_HATS_RULE_DELIVERY_CMD"] = f"{tmp_path / 'pinned' / 'python3'} -m ai_hats.rule_delivery"
    result = subprocess.run(
        ["bash", str(HOOK_PATH)],
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text().split() == ["PINNED"], (
        f"an explicit override must win, got {marker.read_text()!r}"
    )
