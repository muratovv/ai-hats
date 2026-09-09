"""e2e (HATS-757)

flow:   a developer committing python code with multi-line destructive call carrying
        safe-delete marker
cmds:
    git commit -m "marked multi-line cleanup"
expect: pre-commit hook allows multi-line call with relocated safe-delete marker while
        blocking unmarked calls
why:    without multi-line marker parsing, ruff formatting relocates markers and falsely
        blocks legitimate commits
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guards


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-commit-no-raw-destructive.sh"
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _make_repo(tmp_path: Path) -> Path:
    """Ephemeral git repo with the REAL guard wired as the pre-commit hook and
    the exempt ``ai_hats_core/safe_delete.py`` core site (HATS-862 layout)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    _git(repo, "config", "user.email", "t@e.x")
    _git(repo, "config", "user.name", "t")

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / "pre-commit"
    dest.write_text(HOOK.read_text())
    dest.chmod(0o755)

    src = repo / "src" / "ai_hats"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    core = repo / "packages" / "ai-hats-core" / "src" / "ai_hats_core"
    core.mkdir(parents=True)
    (core / "safe_delete.py").write_text("def discard(p):\n    p.unlink()\n")
    return repo


def _commit(repo: Path, msg: str) -> subprocess.CompletedProcess:
    """Run a real ``git commit`` (hooks active, override env stripped)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("AI_HATS_NO_RAW_DESTRUCTIVE_SKIP", None)
    _git(repo, "add", "-A")
    return subprocess.run(
        ["git", "commit", "-m", msg, "--quiet"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.integration
def test_commit_allows_multiline_marked_call(tmp_path: Path):
    """A correctly-marked multi-line call must commit (the HATS-757 bug)."""
    repo = _make_repo(tmp_path)
    (repo / "src" / "ai_hats" / "cleanup.py").write_text(
        "import shutil\n"
        "def f(p):\n"
        "    shutil.rmtree(\n"
        "        p, ignore_errors=True\n"
        "    )  # safe-delete: ok multi-line cleanup\n"
    )
    res = _commit(repo, "marked multi-line call")
    assert res.returncode == 0, f"marked multi-line call must commit; hook stderr:\n{res.stderr}"
    log = _git(repo, "log", "--oneline")
    assert log.stdout.strip(), "commit did not land"


@pytest.mark.integration
def test_commit_blocks_unmarked_multiline_call(tmp_path: Path):
    """An unmarked multi-line call must still be blocked (no over-permission)."""
    repo = _make_repo(tmp_path)
    (repo / "src" / "ai_hats" / "cleanup.py").write_text(
        "import shutil\ndef f(p):\n    shutil.rmtree(\n        p, ignore_errors=True\n    )\n"
    )
    res = _commit(repo, "unmarked multi-line call")
    assert res.returncode != 0, "unmarked multi-line call must be blocked"
    assert "raw destructive call" in res.stderr
