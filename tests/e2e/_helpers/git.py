"""Git helper primitives shared across e2e tests (HATS-1497)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in cwd with check=True and text/capture output."""
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_repo(path: Path, *, branch: str = "main", harden: bool = True) -> None:
    """Initialize a git repository with default email/name and optional hardening."""
    (path / ".gitignore").write_text(".agent/\nai-hats.yaml\n")
    git(path, "init", "-b", branch)
    git(path, "config", "user.email", "t@e")
    git(path, "config", "user.name", "T")
    if harden:
        git(path, "config", "core.hooksPath", "/dev/null")
        git(path, "config", "commit.gpgsign", "false")
    git(path, "add", "-A")
    git(path, "commit", "-m", "init", "--allow-empty")


def commit_file(repo: Path, rel: str | Path, text: str, msg: str = "commit") -> None:
    """Write text to repo / rel, git add it, and git commit."""
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", str(rel))
    git(repo, "commit", "-m", msg)


def head_sha(repo: Path) -> str:
    """Return HEAD commit SHA."""
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def current_branch(repo: Path) -> str:
    """Return current branch name."""
    return git(repo, "branch", "--show-current").stdout.strip()


def branches(repo: Path) -> list[str]:
    """Return list of local branch names."""
    out = git(repo, "branch", "--format=%(refname:short)").stdout
    return [b.strip() for b in out.splitlines() if b.strip()]


def log_subjects(repo: Path) -> list[str]:
    """Return list of commit subjects in log order."""
    out = git(repo, "log", "--format=%s").stdout
    return [s.strip() for s in out.splitlines() if s.strip()]


def worktrees(repo: Path) -> dict[str, Path]:
    """Map branch name -> worktree path for every worktree in repo."""
    out = git(repo, "worktree", "list", "--porcelain").stdout
    res: dict[str, Path] = {}
    cur_path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and cur_path is not None:
            branch = line[len("branch ") :].strip().removeprefix("refs/heads/")
            res[branch] = cur_path
    return res
