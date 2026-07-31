"""HATS-1407 — a gate bypass must leave a durable trace, not just stderr.

Per ``dev_rule_e2e_gate``: the journal is pure bash sourced across a directory
boundary, so the thing most likely to break is the relative path itself. These
tests lay out the REAL install shape — helper at ``.githooks/bypass_journal.sh``,
hook at ``.githooks/<event>.d/<skill>-<basename>`` — and run the hook as a real
subprocess against a throwaway git repo. Running the hook from its source path
instead would resolve ``../bypass_journal.sh`` somewhere else entirely and prove
nothing about production.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
JOURNAL_HELPER = LIB / "hooks/bypass_journal.sh"
PRIVACY_HOOK = LIB / "core/skills/git-mastery/git_hooks/pre-commit-privacy.sh"

JOURNAL_REL = ".git/ai-hats/bypasses.jsonl"

#: Every line must carry these keys — the contract the Python twin also honours.
EXPECTED_FIELDS = {
    "ts",
    "event",
    "hook",
    "kind",
    "reason",
    "head_before",
    "branch",
    "session_id",
    "sha",
}


@pytest.fixture
def gated_repo(tmp_path: Path) -> Path:
    """A git repo wired the way ``install_git_hooks`` wires a real one."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)

    githooks = tmp_path / ".githooks"
    event_d = githooks / "pre-commit.d"
    event_d.mkdir(parents=True)

    helper = githooks / "bypass_journal.sh"
    helper.write_bytes(JOURNAL_HELPER.read_bytes())
    helper.chmod(0o755)

    hook = event_d / "git-mastery-pre-commit-privacy.sh"
    hook.write_bytes(PRIVACY_HOOK.read_bytes())
    hook.chmod(0o755)
    return tmp_path


def _run_hook(repo: Path, **env_overrides: str) -> subprocess.CompletedProcess:
    """Run the installed privacy hook the way the dispatcher runs it."""
    (repo / "note.txt").write_text("nothing secret\n")
    subprocess.run(["git", "add", "note.txt"], cwd=str(repo), check=True)

    env = os.environ.copy()
    env.pop("AI_HATS_PRIVACY_ACK", None)
    env.pop("AI_HATS_SESSION_ID", None)
    env["AI_HATS_HOOK_EVENT"] = "pre-commit"  # the dispatcher exports this
    env.update(env_overrides)

    return subprocess.run(
        ["bash", str(repo / ".githooks/pre-commit.d/git-mastery-pre-commit-privacy.sh")],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _journal_lines(repo: Path) -> list[dict]:
    path = repo / JOURNAL_REL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.integration
def test_tripped_hatch_is_recorded(gated_repo: Path):
    """The defect: AI_HATS_PRIVACY_ACK=1 skipped the gate and left only stderr."""
    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1")
    assert res.returncode == 0, res.stderr

    lines = _journal_lines(gated_repo)
    assert len(lines) == 1, f"expected exactly one journal line, got {lines}\n{res.stderr}"

    entry = lines[0]
    assert set(entry) == EXPECTED_FIELDS, f"field drift: {sorted(entry)}"
    assert entry["kind"] == "hatch"
    assert entry["reason"] == "AI_HATS_PRIVACY_ACK"
    assert entry["event"] == "pre-commit"
    assert entry["hook"] == "git-mastery-pre-commit-privacy.sh"


@pytest.mark.integration
def test_session_id_is_captured_when_present(gated_repo: Path):
    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1", AI_HATS_SESSION_ID="sid-42")
    assert res.returncode == 0, res.stderr
    assert _journal_lines(gated_repo)[0]["session_id"] == "sid-42"


@pytest.mark.integration
def test_session_id_is_empty_for_a_human_in_a_terminal(gated_repo: Path):
    """A bypass outside any agent session must still be recorded, just unattributed."""
    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1")
    assert res.returncode == 0, res.stderr
    assert _journal_lines(gated_repo)[0]["session_id"] == ""


@pytest.mark.integration
def test_pre_commit_leaves_sha_empty_for_the_post_commit_stamp(gated_repo: Path):
    """pre-commit runs before the commit object exists; `head_before` pins the parent."""
    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1")
    assert res.returncode == 0, res.stderr
    assert _journal_lines(gated_repo)[0]["sha"] == ""


@pytest.mark.integration
def test_a_clean_run_records_nothing(gated_repo: Path):
    """Negative control — the journal must mean 'a gate was bypassed', nothing else."""
    res = _run_hook(gated_repo)
    assert res.returncode == 0, res.stderr
    assert _journal_lines(gated_repo) == []


@pytest.mark.integration
def test_a_missing_helper_fails_loud_not_silent(gated_repo: Path):
    """The journal's own failure mode must not be the defect it exists to remove."""
    (gated_repo / ".githooks/bypass_journal.sh").unlink()

    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1")
    assert res.returncode == 0, "a broken journal must not block the commit"
    assert "NOT RECORDED" in res.stderr, res.stderr
    assert _journal_lines(gated_repo) == []
