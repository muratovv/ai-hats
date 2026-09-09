"""e2e (HATS-437, HATS-633)

flow:   a maintainer pushes, and git hands the pre-push shared-state hook the
        refspec on stdin before anything leaves the machine
cmds:
    git push                                       # fast-forward -> allowed
    git push --force                               # rewrites history -> blocked
    git push origin :branch                        # deletion -> allowed
    AI_HATS_SHARED_STATE_ACK=1 git push --force    # ack -> allowed
expect: a fast-forward, a branch deletion, a brand-new branch and an empty stdin
        all pass; a non-fast-forward exits 1, and the refusal names
        `rule_pause_before_shared_state_write` and says "Do NOT retry" rather
        than failing bare; the env ack overrides the block
why:    the hook is pure bash driven by git over stdin, so nothing in-process
        reaches it — and a hook that blocks a legal fast-forward is as broken as
        one that waves a force-push through. The PreToolUse half is unit-tested
        in tests/test_shared_state_guard.py; only this half needs a real repo.
"""

from __future__ import annotations
from _helpers.git import git as _git_helper

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.guards


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PREPUSH_HOOK = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh"
)


# --- Git pre-push hook -----------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return _git_helper(cwd, *args).stdout.strip()


@pytest.fixture
def repo_with_two_commits(tmp_path: Path) -> Path:
    """Repo whose HEAD~1 → HEAD chain we can hand to the pre-push hook."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    (tmp_path / "a").write_text("1")
    subprocess.run(["git", "add", "a"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "one", "--quiet"], cwd=str(tmp_path), check=True)
    (tmp_path / "b").write_text("2")
    subprocess.run(["git", "add", "b"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "two", "--quiet"], cwd=str(tmp_path), check=True)
    return tmp_path


@pytest.mark.integration
def test_prepush_allows_fast_forward(repo_with_two_commits: Path):
    local = _git(repo_with_two_commits, "rev-parse", "HEAD")
    remote = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    stdin = f"refs/heads/master {local} refs/heads/master {remote}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_prepush_blocks_non_fast_forward(repo_with_two_commits: Path):
    """Swap local/remote so remote sha is NOT an ancestor of local sha."""
    older = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    newer = _git(repo_with_two_commits, "rev-parse", "HEAD")
    # Local is OLDER, remote is NEWER → would rewrite remote history.
    stdin = f"refs/heads/master {older} refs/heads/master {newer}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
        env={**os.environ, "AI_HATS_SHARED_STATE_ACK": ""},
    )
    res.check_returncode if False else None  # silence unused-import linters
    assert res.returncode == 1, res.stderr
    assert "non-fast-forward" in res.stderr


@pytest.mark.integration
def test_prepush_block_carries_recovery_guidance(repo_with_two_commits: Path):
    """HATS-633 — pre-push denial carries the same recovery guidance shape as
    the PreToolUse guard: names the rule, says do-not-retry."""
    older = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    newer = _git(repo_with_two_commits, "rev-parse", "HEAD")
    stdin = f"refs/heads/master {older} refs/heads/master {newer}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
        env={**os.environ, "AI_HATS_SHARED_STATE_ACK": ""},
    )
    assert res.returncode == 1, res.stderr
    assert "rule_pause_before_shared_state_write" in res.stderr
    assert "Do NOT retry" in res.stderr


@pytest.mark.integration
def test_prepush_ack_overrides(repo_with_two_commits: Path):
    older = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    newer = _git(repo_with_two_commits, "rev-parse", "HEAD")
    stdin = f"refs/heads/master {older} refs/heads/master {newer}\n"
    env = os.environ.copy()
    env["AI_HATS_SHARED_STATE_ACK"] = "1"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
        env=env,
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_prepush_allows_branch_deletion(repo_with_two_commits: Path):
    """Deletion (local sha=0) must not be treated as force."""
    head = _git(repo_with_two_commits, "rev-parse", "HEAD")
    zero = "0" * 40
    stdin = f"refs/heads/foo {zero} refs/heads/foo {head}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_prepush_allows_new_branch(repo_with_two_commits: Path):
    """New ref (remote sha=0) must not be treated as force."""
    head = _git(repo_with_two_commits, "rev-parse", "HEAD")
    zero = "0" * 40
    stdin = f"refs/heads/foo {head} refs/heads/foo {zero}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin,
        cwd=str(repo_with_two_commits),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_prepush_allows_empty_stdin(tmp_path: Path):
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input="",
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert res.returncode == 0
