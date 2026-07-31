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

GM = "core/skills/git-mastery/git_hooks"

#: (hook relpath, git event, hatch env var). Every git-hook hatch in the repo —
#: `test_bypass_journal_coverage.py` fails if one is added without a row here.
GIT_HOOK_HATCHES = [
    (f"{GM}/pre-commit-privacy.sh", "pre-commit", "AI_HATS_PRIVACY_ACK"),
    (f"{GM}/pre-commit-smoke.sh", "pre-commit", "AI_HATS_SMOKE_SKIP"),
    (f"{GM}/pre-commit-docs-index.sh", "pre-commit", "AI_HATS_DOCS_INDEX_ACK"),
    (f"{GM}/pre-commit-no-raw-destructive.sh", "pre-commit", "AI_HATS_NO_RAW_DESTRUCTIVE_SKIP"),
    (f"{GM}/pre-push-shared-state.sh", "pre-push", "AI_HATS_SHARED_STATE_ACK"),
    (
        "usage/skills/rule-delivery-gate/git_hooks/pre-commit-rule-delivery.sh",
        "pre-commit",
        "AI_HATS_RULE_DELIVERY_ACK",
    ),
    (
        "usage/skills/skill-lint-gate/git_hooks/pre-commit-skill-lint.sh",
        "pre-commit",
        "AI_HATS_SKILL_LINT_ACK",
    ),
]

JOURNAL_REL = ".git/ai-hats/bypasses.jsonl"

#: Every line must carry these keys — the contract the Python twin also honours.
EXPECTED_FIELDS = {
    "ts",
    "event",
    "hook",
    "kind",
    "reason",
    "cmd",
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


def _rev(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


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
def test_head_before_is_a_sha_or_empty_never_the_literal_head(gated_repo: Path):
    """`$(cmd || printf \'\')` keeps a failed git\'s stdout — "HEAD" landed in the field."""
    res = _run_hook(gated_repo, AI_HATS_PRIVACY_ACK="1")
    assert res.returncode == 0, res.stderr
    entry = _journal_lines(gated_repo)[0]
    assert entry["head_before"] == "", entry
    assert entry["branch"] == "", entry


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


# --- every git-hook hatch, in the real install layout ------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    ("hook_rel", "event", "hatch"),
    GIT_HOOK_HATCHES,
    ids=[Path(h).stem for h, _, _ in GIT_HOOK_HATCHES],
)
def test_every_git_hook_hatch_is_recorded(tmp_path: Path, hook_rel: str, event: str, hatch: str):
    """One row per hatch: tripping it must leave a line naming that variable."""
    repo = tmp_path
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)

    githooks = repo / ".githooks"
    event_d = githooks / f"{event}.d"
    event_d.mkdir(parents=True)
    (githooks / "bypass_journal.sh").write_bytes(JOURNAL_HELPER.read_bytes())
    hook = event_d / f"skill-{Path(hook_rel).name}"
    hook.write_bytes((LIB / hook_rel).read_bytes())
    hook.chmod(0o755)

    env = os.environ.copy()
    for key in list(env):
        if key.startswith("AI_HATS_"):
            env.pop(key)
    env["AI_HATS_HOOK_EVENT"] = event
    env[hatch] = "1"

    res = subprocess.run(
        ["bash", str(hook)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=20,
        input="",
        env=env,
    )
    assert res.returncode == 0, f"hatch should skip the gate, not fail\n{res.stderr}"

    lines = _journal_lines(repo)
    assert len(lines) == 1, f"expected one line, got {lines}\nstderr: {res.stderr}"
    assert lines[0]["reason"] == hatch
    assert lines[0]["kind"] == "hatch"
    assert lines[0]["event"] == event


# --- the post-commit stamp closes the join to a real commit ------------------


def _wire_dispatcher(repo: Path, event: str, hooks: list[Path]) -> None:
    """Install a minimal `.githooks/` the way the real installer does."""
    githooks = repo / ".githooks"
    event_d = githooks / f"{event}.d"
    event_d.mkdir(parents=True, exist_ok=True)
    (githooks / "bypass_journal.sh").write_bytes(JOURNAL_HELPER.read_bytes())
    for src in hooks:
        dest = event_d / f"git-mastery-{src.name}"
        dest.write_bytes(src.read_bytes())
        dest.chmod(0o755)
    dispatcher = githooks / event
    dispatcher.write_bytes(
        (REPO_ROOT / "src/ai_hats/templates/githooks/dispatcher.sh").read_bytes()
    )
    dispatcher.chmod(0o755)


@pytest.mark.integration
def test_post_commit_stamps_the_sha_onto_the_bypass(tmp_path: Path):
    """The card's question: was THIS commit gated? Unstamped, the journal cannot say."""
    repo = tmp_path
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)

    _wire_dispatcher(repo, "pre-commit", [LIB / f"{GM}/pre-commit-privacy.sh"])
    _wire_dispatcher(repo, "post-commit", [LIB / f"{GM}/post-commit-bypass-stamp.sh"])
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(repo), check=True)

    (repo / "a.txt").write_text("first\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    # GIT_* must not leak into a nested git (HATS-886); the hook re-derives its own.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["AI_HATS_PRIVACY_ACK"] = "1"
    subprocess.run(["git", "commit", "-q", "-m", "bypassed"], cwd=str(repo), check=True, env=env)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()

    entries = _journal_lines(repo)
    assert len(entries) == 1, entries
    assert entries[0]["sha"] == head, "the bypass is not attributable to its commit"


# --- the consumer: a journal nobody reads is a sensor nobody consumes --------


@pytest.mark.integration
def test_pre_push_reports_bypasses_in_the_pushed_range(tmp_path: Path):
    repo = tmp_path
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    (repo / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(repo), check=True)
    base = _rev(repo, "HEAD")

    (repo / "b.txt").write_text("two\n")
    subprocess.run(["git", "add", "b.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "bypassed"], cwd=str(repo), check=True)
    head = _rev(repo, "HEAD")

    journal = repo / ".git/ai-hats"
    journal.mkdir(parents=True)
    (journal / "bypasses.jsonl").write_text(
        json.dumps({f: "" for f in EXPECTED_FIELDS} | {"reason": "AI_HATS_SMOKE_SKIP", "sha": head})
        + "\n"
    )

    hook = LIB / f"{GM}/pre-push-bypass-report.sh"
    res = subprocess.run(
        ["bash", str(hook), "origin", "https://example.invalid/r.git"],
        cwd=str(repo),
        input=f"refs/heads/master {head} refs/heads/master {base}\n",
        capture_output=True,
        text=True,
        timeout=20,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )

    assert res.returncode == 0, "reporting is not blocking — the hatch was deliberate"
    assert "AI_HATS_SMOKE_SKIP" in res.stderr, res.stderr
    assert "1 gate bypass" in res.stderr, res.stderr


@pytest.mark.integration
def test_pre_push_is_silent_when_the_pushed_range_is_clean(tmp_path: Path):
    """Negative control — the report must mean 'these commits skipped a gate'."""
    repo = tmp_path
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)
    (repo / "a.txt").write_text("one\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=str(repo), check=True)
    base = _rev(repo, "HEAD")
    (repo / "b.txt").write_text("two\n")
    subprocess.run(["git", "add", "b.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "clean"], cwd=str(repo), check=True)
    head = _rev(repo, "HEAD")

    journal = repo / ".git/ai-hats"
    journal.mkdir(parents=True)
    # A bypass on an UNRELATED commit must not be reported for this range.
    (journal / "bypasses.jsonl").write_text(
        json.dumps({f: "" for f in EXPECTED_FIELDS} | {"sha": "d" * 40}) + "\n"
    )

    res = subprocess.run(
        ["bash", str(LIB / f"{GM}/pre-push-bypass-report.sh"), "origin", "url"],
        cwd=str(repo),
        input=f"refs/heads/master {head} refs/heads/master {base}\n",
        capture_output=True,
        text=True,
        timeout=20,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )
    assert res.returncode == 0
    assert "gate bypass" not in res.stderr, res.stderr


@pytest.mark.integration
def test_pre_bash_shared_state_guard_records_cmd_and_session_id(tmp_path: Path):
    """3a and 3b: pre_bash_shared_state_guard records cmd and session_id from stdin JSON payload."""
    repo = tmp_path
    subprocess.run(["git", "init", "--quiet"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(repo), check=True)

    guard = LIB / "hooks/pre_bash_shared_state_guard.sh"
    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin master"},
            "session_id": "test-session-123",
        }
    )
    res = subprocess.run(
        ["bash", str(guard)],
        cwd=str(repo),
        input=payload,
        capture_output=True,
        text=True,
        env=dict(os.environ) | {"AI_HATS_SHARED_STATE_ACK": "1"},
    )
    assert res.returncode == 0
    lines = _journal_lines(repo)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["cmd"] == "git push origin master"
    assert entry["session_id"] == "test-session-123"
