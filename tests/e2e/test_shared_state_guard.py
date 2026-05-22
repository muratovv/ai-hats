"""HATS-437 — end-to-end behaviour of the shared-state-guard hooks.

Per ``dev_rule_e2e_gate``: the two hook scripts (PreToolUse + git pre-push)
are pure-bash surfaces that the unit suite cannot meaningfully exercise.
This file invokes the scripts as a real subprocess to cover:

  * PreToolUse hook: non-TTY block on irreversible, ack-override, pass-through
    for safe / shared / empty payloads, chained-command detection.
  * Git pre-push hook: non-fast-forward detection, deletion / new-branch
    short-circuit, ack-override.

Slow only because of subprocess spin-up (~ms each, no pip install).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRETOOL_HOOK = REPO_ROOT / "library/hooks/pre_bash_shared_state_guard.sh"
PREPUSH_HOOK = (
    REPO_ROOT
    / "library/core/skills/git-mastery/git_hooks/pre-push-shared-state.sh"
)


def _run(script: Path, *, stdin: str, env: dict | None = None, timeout: int = 5):
    base_env = os.environ.copy()
    # Strip any ambient ack so tests don't accidentally inherit one from
    # the parent shell (e.g. when developer ran with the override locally).
    base_env.pop("AI_HATS_SHARED_STATE_ACK", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


# --- PreToolUse hook -------------------------------------------------------


@pytest.mark.integration
def test_pretool_blocks_gh_pr_merge_non_tty():
    payload = (
        '{"hook_event_name":"PreToolUse","tool_name":"Bash",'
        '"tool_input":{"command":"gh pr merge 5 --merge --delete-branch"}}'
    )
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 2, res.stderr
    assert "BLOCKED" in res.stderr
    assert "gh pr merge" in res.stderr


@pytest.mark.integration
def test_pretool_blocks_git_push_force_non_tty():
    payload = '{"tool_input":{"command":"git push --force origin master"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 2, res.stderr


@pytest.mark.integration
def test_pretool_blocks_short_flag_force():
    payload = '{"tool_input":{"command":"git push -f origin master"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 2, res.stderr


@pytest.mark.integration
def test_pretool_blocks_force_with_lease():
    payload = '{"tool_input":{"command":"git push --force-with-lease origin foo"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 2, res.stderr


@pytest.mark.integration
def test_pretool_blocks_chained_irreversible():
    """Catches the actual HYP-026/027 incident pattern (compound Bash call)."""
    payload = (
        '{"tool_input":{"command":'
        '"git pull && gh pr merge 9 --merge --delete-branch && git push"}}'
    )
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 2, res.stderr


@pytest.mark.integration
def test_pretool_ack_overrides_block():
    payload = '{"tool_input":{"command":"gh pr merge 5"}}'
    res = _run(PRETOOL_HOOK, stdin=payload, env={"AI_HATS_SHARED_STATE_ACK": "1"})
    assert res.returncode == 0, res.stderr
    assert "AI_HATS_SHARED_STATE_ACK=1" in res.stderr


@pytest.mark.integration
def test_pretool_allows_regular_push():
    """Regular `git push` is shared (rule covers it) but hook must not block."""
    payload = '{"tool_input":{"command":"git push origin master"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_pretool_allows_safe_command():
    payload = '{"tool_input":{"command":"ls -la"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_pretool_allows_empty_payload():
    """Hook must not crash on empty stdin (test invocations, harness no-ops)."""
    res = _run(PRETOOL_HOOK, stdin="")
    assert res.returncode == 0


@pytest.mark.integration
def test_pretool_allows_non_bash_payload():
    """tool_input without a `.command` field — allow with no fuss."""
    payload = '{"tool_input":{"file_path":"/tmp/foo"}}'
    res = _run(PRETOOL_HOOK, stdin=payload)
    assert res.returncode == 0


# --- Git pre-push hook -----------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo_with_two_commits(tmp_path: Path) -> Path:
    """Repo whose HEAD~1 → HEAD chain we can hand to the pre-push hook."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)
    (tmp_path / "a").write_text("1")
    subprocess.run(["git", "add", "a"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "one", "--quiet"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "b").write_text("2")
    subprocess.run(["git", "add", "b"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "two", "--quiet"], cwd=str(tmp_path), check=True
    )
    return tmp_path


@pytest.mark.integration
def test_prepush_allows_fast_forward(repo_with_two_commits: Path):
    local = _git(repo_with_two_commits, "rev-parse", "HEAD")
    remote = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    stdin = f"refs/heads/master {local} refs/heads/master {remote}\n"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin, cwd=str(repo_with_two_commits),
        capture_output=True, text=True, timeout=5,
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
        input=stdin, cwd=str(repo_with_two_commits),
        capture_output=True, text=True, timeout=5,
        env={**os.environ, "AI_HATS_SHARED_STATE_ACK": ""},
    )
    res.check_returncode if False else None  # silence unused-import linters
    assert res.returncode == 1, res.stderr
    assert "non-fast-forward" in res.stderr


@pytest.mark.integration
def test_prepush_ack_overrides(repo_with_two_commits: Path):
    older = _git(repo_with_two_commits, "rev-parse", "HEAD~1")
    newer = _git(repo_with_two_commits, "rev-parse", "HEAD")
    stdin = f"refs/heads/master {older} refs/heads/master {newer}\n"
    env = os.environ.copy()
    env["AI_HATS_SHARED_STATE_ACK"] = "1"
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input=stdin, cwd=str(repo_with_two_commits),
        capture_output=True, text=True, timeout=5, env=env,
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
        input=stdin, cwd=str(repo_with_two_commits),
        capture_output=True, text=True, timeout=5,
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
        input=stdin, cwd=str(repo_with_two_commits),
        capture_output=True, text=True, timeout=5,
    )
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_prepush_allows_empty_stdin(tmp_path: Path):
    res = subprocess.run(
        ["bash", str(PREPUSH_HOOK)],
        input="", cwd=str(tmp_path),
        capture_output=True, text=True, timeout=5,
    )
    assert res.returncode == 0
