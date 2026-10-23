"""Behavioural tests for the task-merge PreToolUse guard (HATS-2062).

A raw `git merge` of a task branch into a base branch blinds the supervisor's
review diff (merge-base becomes the branch tip — PROX-619). The guard hard-
denies that spelling and routes to `ai-hats wt merge`, which carries the
card-state check and the supervisor's consent. No ack flag opens the deny:
an early merge approved through the managed path is a recorded human decision.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = (
    Path(__file__).resolve().parents[1]
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/safety-guard/hooks"
)
GUARD = HOOKS_DIR / "pre_bash_task_merge_guard.sh"
CLASSIFIER = HOOKS_DIR / "task_merge_classifier.sh"


def _classify(command: str) -> str:
    return subprocess.run(
        [str(CLASSIFIER), command], capture_output=True, text=True, check=True
    ).stdout.strip()


# --- Classifier (pure string, no git) -------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git merge task/prox-619",
        "git merge --no-ff task/hats-2062",
        "git -C /repo merge task/PROX-619",
        "git merge task/prox-619 && git push",
        "cd sub; git merge task/a-1",
    ],
)
def test_classifier_flags_task_merge(command: str) -> None:
    assert _classify(command) == "task-merge"


@pytest.mark.parametrize(
    "command",
    [
        "git checkout master && git merge task/prox-619",
        "git switch main && git merge --no-ff task/hats-2062",
        "git checkout -f master && git merge task/prox-619",
        "git merge task/prox-619; git checkout master",
    ],
)
def test_classifier_flags_merge_with_base_switch(command: str) -> None:
    assert _classify(command) == "task-merge-base"


@pytest.mark.parametrize(
    "command",
    [
        "git merge master",  # base into the current branch — review diff survives
        "git merge origin/main",
        "git merge task/other",  # not a task ref (no ticket number)
        "git merge task/prox-619ish",  # ref-like prefix, not a ref
        "git push origin master",
        "git checkout master",
        "echo git merge task/prox-619",  # prose, not a merge command
    ],
)
def test_classifier_passes_non_task_merges(command: str) -> None:
    assert _classify(command) == "safe"


# --- Guard (payload in, decision out) --------------------------------------


def _run_guard(
    command: str, *, cwd: Path | None = None, claude: bool = True
) -> subprocess.CompletedProcess[str]:
    """Drive the guard. ``claude`` selects the refusal dialect, as in the
    shared-state guard: a payload with ``hook_event_name`` gets a decision
    object, anything else gets exit 2 on stderr."""
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    payload: dict[str, object] = {"tool_input": {"command": command}}
    if cwd is not None:
        payload["cwd"] = str(cwd)
    if claude:
        payload["hook_event_name"] = "PreToolUse"
    return subprocess.run(
        [str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo_on_master(hook_repo: Path) -> Path:
    _git(hook_repo, "commit", "-q", "--allow-empty", "-m", "init")
    _git(hook_repo, "branch", "-M", "master")
    return hook_repo


def _deny_reason(run: subprocess.CompletedProcess[str]) -> str:
    assert run.returncode == 0, run.stderr
    decision = json.loads(run.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


def test_deny_merge_on_master(repo_on_master: Path) -> None:
    reason = _deny_reason(_run_guard("git merge task/prox-619", cwd=repo_on_master))
    assert "ai-hats wt merge" in reason


def test_deny_chained_checkout_from_any_branch(repo_on_master: Path) -> None:
    _git(repo_on_master, "checkout", "-q", "-b", "task/hats-2062")
    reason = _deny_reason(
        _run_guard("git checkout master && git merge task/prox-619", cwd=repo_on_master)
    )
    assert "ai-hats wt merge" in reason


def test_allow_merge_into_a_task_branch(repo_on_master: Path) -> None:
    _git(repo_on_master, "checkout", "-q", "-b", "task/hats-2062")
    run = _run_guard("git merge task/prox-619", cwd=repo_on_master)
    assert run.returncode == 0
    assert run.stdout == "", run.stderr


def test_deny_dash_c_into_master_from_a_task_branch(repo_on_master: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo_on_master, "worktree", "add", "-q", "-b", "task/hats-2062", str(wt))
    reason = _deny_reason(_run_guard(f"git -C {repo_on_master} merge task/prox-619", cwd=wt))
    assert "ai-hats wt merge" in reason


def test_deny_cd_into_master_from_a_task_branch(repo_on_master: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo_on_master, "worktree", "add", "-q", "-b", "task/hats-2062", str(wt))
    reason = _deny_reason(_run_guard(f"cd {repo_on_master} && git merge task/prox-619", cwd=wt))
    assert "ai-hats wt merge" in reason


def test_unreadable_merge_command_is_refused(repo_on_master: Path) -> None:
    reason = _deny_reason(_run_guard("git merge task/prox-619 'unterminated", cwd=repo_on_master))
    assert "ai-hats wt merge" in reason


def test_allow_merge_of_base(repo_on_master: Path) -> None:
    run = _run_guard("git merge master", cwd=repo_on_master)
    assert run.returncode == 0
    assert run.stdout == ""


def test_allow_outside_a_repo(tmp_path: Path) -> None:
    # Not a git repo: the merge would fail anyway — allow with a breadcrumb.
    run = _run_guard("git merge task/prox-619", cwd=tmp_path)
    assert run.returncode == 0
    assert run.stdout == ""


def test_deny_hard_without_claude_dialect(repo_on_master: Path) -> None:
    run = _run_guard("git merge task/prox-619", cwd=repo_on_master, claude=False)
    assert run.returncode == 2
    assert "BLOCKED" in run.stderr
    assert "ai-hats wt merge" in run.stderr


def test_no_payload_allows() -> None:
    run = subprocess.run([str(GUARD)], input="", capture_output=True, text=True, timeout=20)
    assert run.returncode == 0


def test_no_ack_flag_opens_the_deny(repo_on_master: Path) -> None:
    """The deny text must not name an AI_HATS_*_ACK hatch: consent for an early
    merge lives in the managed path, not in an env flag the agent could carry."""
    reason = _deny_reason(_run_guard("git merge task/prox-619", cwd=repo_on_master))
    assert "ACK" not in reason
