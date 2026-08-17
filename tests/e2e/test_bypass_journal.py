"""e2e (HATS-1407)

flow:   a developer committing code with gate bypass environment variables enabled
cmds:
    # with AI_HATS_PRIVACY_ACK=1 enabled
    git commit -m "bypass commit"
expect: git pre-commit hook logs bypass entry to journal and post-commit stamps
        commit SHA
why:    without bypass logging, gate overrides leave no audit records in repository
        history
"""

from __future__ import annotations
from _helpers.git import init_repo

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
    from _helpers.git import git

    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    git(tmp_path, "config", "commit.gpgsign", "false")

    # HATS-1337: nothing is copied any more — a gate runs in place from the
    # library, with the journal handed to it by the dispatcher as env.
    return tmp_path


def _run_hook(repo: Path, **env_overrides: str) -> subprocess.CompletedProcess:
    """Run the installed privacy hook the way the dispatcher runs it."""
    (repo / "note.txt").write_text("nothing secret\n")
    subprocess.run(["git", "add", "note.txt"], cwd=str(repo), check=True)

    env = os.environ.copy()
    env.pop("AI_HATS_PRIVACY_ACK", None)
    env.pop("AI_HATS_SESSION_ID", None)
    env["AI_HATS_HOOK_EVENT"] = "pre-commit"  # the dispatcher exports this
    env["AI_HATS_BYPASS_JOURNAL"] = str(JOURNAL_HELPER)  # ...and this (HATS-1337)
    env.update(env_overrides)

    return subprocess.run(
        ["bash", str(PRIVACY_HOOK)],
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
    # HATS-1337: gates run in place, so the journal names the real script
    # rather than the retired flattened copy `<skill>-<basename>`.
    assert entry["hook"] == "pre-commit-privacy.sh"


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
    # HATS-1337: no copy to delete — an unreachable journal is now a resolved
    # path that does not exist (and a relative fallback that misses too).
    res = _run_hook(
        gated_repo,
        AI_HATS_PRIVACY_ACK="1",
        AI_HATS_BYPASS_JOURNAL=str(gated_repo / "nope" / "bypass_journal.sh"),
    )
    assert res.returncode == 0, "a broken journal must not block the commit"
    assert "NOT RECORDED" in res.stderr, res.stderr
    assert _journal_lines(gated_repo) == []


@pytest.mark.integration
def test_a_lone_shell_helper_without_its_writer_fails_loud_not_silent(gated_repo: Path):
    """HATS-1486: the shell side only wraps — the writer is a sibling .py.

    Resolving to a `bypass_journal.sh` with no `bypass_journal.py` beside it is a
    reachable state (a copy taken out of its directory), and it must fail the way
    every other unreachable-journal path does: loudly, without blocking.
    """
    orphan_dir = gated_repo / "orphan"
    orphan_dir.mkdir()
    orphan = orphan_dir / "bypass_journal.sh"
    orphan.write_bytes(JOURNAL_HELPER.read_bytes())

    res = _run_hook(
        gated_repo,
        AI_HATS_PRIVACY_ACK="1",
        AI_HATS_BYPASS_JOURNAL=str(orphan),
    )
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
    init_repo(repo)

    # HATS-1337: gates run in place from the library, journal handed over as env.
    hook = LIB / hook_rel

    env = os.environ.copy()
    for key in list(env):
        if key.startswith("AI_HATS_"):
            env.pop(key)
    env["AI_HATS_HOOK_EVENT"] = event
    env["AI_HATS_BYPASS_JOURNAL"] = str(JOURNAL_HELPER)
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


def _ai_hats_pin() -> dict[str, str]:
    """Env making the installed stub delegate to THIS checkout's ai-hats.

    HATS-1337: the stub is a bootstrap — without a resolvable install it fails
    open and runs nothing, so a fixture that hand-builds `.githooks/` must supply
    one or every assertion below passes vacuously.
    """
    import sys

    from _helpers.env import checkout_pythonpath
    from ai_hats.paths import ENV_AI_HATS_VENV

    return {
        "PYTHONPATH": checkout_pythonpath(REPO_ROOT),
        ENV_AI_HATS_VENV: str(Path(sys.executable).parent.parent),
    }


def _wire_dispatcher(repo: Path, event: str, hooks: list[Path]) -> None:
    """A dispatcher plus gates on the `<event>.d/` drop-in path.

    HATS-1337: the installer no longer copies gates, and this project has no
    ai-hats install for the dispatcher to resolve against — so the gates ride
    the drop-in path the dispatcher still reads, and the caller exports
    AI_HATS_BYPASS_JOURNAL the way a resolved run would.
    """
    githooks = repo / ".githooks"
    event_d = githooks / f"{event}.d"
    event_d.mkdir(parents=True, exist_ok=True)
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
    init_repo(repo)

    _wire_dispatcher(repo, "pre-commit", [LIB / f"{GM}/pre-commit-privacy.sh"])
    _wire_dispatcher(repo, "post-commit", [LIB / f"{GM}/post-commit-bypass-stamp.sh"])
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(repo), check=True)

    (repo / "a.txt").write_text("first\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    # GIT_* must not leak into a nested git (HATS-886); the hook re-derives its own.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["AI_HATS_PRIVACY_ACK"] = "1"
    env["AI_HATS_BYPASS_JOURNAL"] = str(JOURNAL_HELPER)
    env.update(_ai_hats_pin())
    subprocess.run(["git", "commit", "-q", "-m", "bypassed"], cwd=str(repo), check=True, env=env)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()

    entries = _journal_lines(repo)
    assert len(entries) == 1, entries
    assert entries[0]["sha"] == head, "the bypass is not attributable to its commit"


@pytest.mark.integration
def test_stamp_preserves_unparseable_journal_lines_verbatim(tmp_path: Path):
    """HATS-1486: awk passed junk through line-wise; the python stamper must too.

    A journal can hold a line no parser accepts — that is exactly what the old
    shell escaper produced. Rewriting the file must not drop it: the stamper is
    not a validator, and losing a bypass record is the defect this file removes.
    """
    repo = tmp_path
    init_repo(repo)

    _wire_dispatcher(repo, "pre-commit", [LIB / f"{GM}/pre-commit-privacy.sh"])
    _wire_dispatcher(repo, "post-commit", [LIB / f"{GM}/post-commit-bypass-stamp.sh"])
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(repo), check=True)

    journal_path = repo / JOURNAL_REL
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    broken_line = "THIS IS NOT VALID JSON {"
    journal_path.write_text(broken_line + "\n")

    (repo / "a.txt").write_text("first\n")
    subprocess.run(["git", "add", "a.txt"], cwd=str(repo), check=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["AI_HATS_PRIVACY_ACK"] = "1"
    env["AI_HATS_BYPASS_JOURNAL"] = str(JOURNAL_HELPER)
    env.update(_ai_hats_pin())
    subprocess.run(["git", "commit", "-q", "-m", "bypassed"], cwd=str(repo), check=True, env=env)

    raw_lines = journal_path.read_text().splitlines()
    assert len(raw_lines) == 2, raw_lines
    assert raw_lines[0] == broken_line, "the unparseable line was not preserved"
    assert json.loads(raw_lines[1])["sha"] != "", "the valid row was not stamped"


# --- the consumer: a journal nobody reads is a sensor nobody consumes --------


@pytest.mark.integration
def test_pre_push_reports_bypasses_in_the_pushed_range(tmp_path: Path):
    repo = tmp_path
    init_repo(repo)
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
    init_repo(repo)
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
    init_repo(repo)

    guard = LIB / "core/skills/safety-guard/hooks/pre_bash_shared_state_guard.sh"
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
        env=dict(os.environ) | {"AI_HATS_SHARED_STATE_ACK": "1"} | _ai_hats_pin(),
    )
    assert res.returncode == 0
    lines = _journal_lines(repo)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["cmd"] == "git push origin master"
    assert entry["session_id"] == "test-session-123"


# ----- HATS-1597: a gate SKIPPED by the dispatcher is a bypass too -------------


@pytest.mark.integration
def test_a_gate_the_dispatcher_could_not_run_is_recorded_as_fail_open(gated_repo: Path):
    """A skipped gate is a disarmed gate, and ADR-0020 D2 forbids that happening
    SILENTLY — that row is the machine form of ADR-0019 D4's anti-disarm rule.
    Refusing outright would wedge the commit (D3) and push the human to
    `--no-verify`, which disarms the whole chain; recording keeps the skip
    visible where `pre-push-bypass-report.sh` reads it.
    """
    from ai_hats.githooks_run import record_fail_open

    record_fail_open(
        JOURNAL_HELPER,
        reason="s: 'git_hooks/g.sh' is not executable",
        event="pre-commit",
        project_dir=gated_repo,
    )

    lines = _journal_lines(gated_repo)
    assert len(lines) == 1, f"the skip left no journal line: {lines}"
    entry = lines[0]
    assert set(entry) == EXPECTED_FIELDS, f"field drift: {sorted(entry)}"
    assert entry["kind"] == "fail_open"
    assert entry["event"] == "pre-commit"
    assert "not executable" in entry["reason"] and "g.sh" in entry["reason"]


@pytest.mark.integration
def test_the_skip_is_journalled_where_it_is_told_not_where_the_process_stands(
    gated_repo: Path, tmp_path: Path
):
    """HATS-1686: the project is TOLD, never inferred from cwd.

    Inferring is why a unit test's synthetic skips landed in the maintainer's own
    audit journal — 236 rows that `pre-push-bypass-report.sh` then showed the
    reviewer, matched by SHA and indistinguishable from real ones. Standing
    somewhere else entirely is the only way to prove the caller decides.
    """
    from ai_hats.githooks_run import record_fail_open

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=elsewhere, check=True)  # noqa: S603, S607

    cwd = os.getcwd()
    os.chdir(elsewhere)
    try:
        record_fail_open(
            JOURNAL_HELPER, reason="told, not inferred", event="pre-commit", project_dir=gated_repo
        )
    finally:
        os.chdir(cwd)

    lines = _journal_lines(gated_repo)
    assert len(lines) == 1, f"the named project got no row: {lines}"
    assert lines[0]["reason"] == "told, not inferred"
    stray = elsewhere / ".git" / "ai-hats" / "bypasses.jsonl"
    assert not stray.exists(), f"the row landed in the process's own repo: {stray}"
