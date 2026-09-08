"""e2e (HATS-1634)

flow:   a developer committing a file the privacy gate refuses
cmds:
    git commit -m wip
expect: the commit is blocked and the refusal is recorded as a catch row
why:    a git gate's refusal lives only in stderr, so nothing could count how
        often the tier fires or notice it silently stopping firing
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from _helpers.git import git as _git

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GATE = (
    REPO_ROOT
    / "packages/ai-hats-library/src/ai_hats_library/core/skills/git-mastery"
    / "git_hooks/pre-commit-privacy.sh"
)


@pytest.fixture
def repo(tmp_path):
    """A real repo with the privacy gate installed as its pre-commit hook."""
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-b", "master")
    _git(project, "config", "user.email", "t@t.io")
    _git(project, "config", "user.name", "t")
    (project / "seed.txt").write_text("seed\n")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "init")

    hook = project / ".git/hooks/pre-commit"
    hook.write_text(f'#!/usr/bin/env bash\nexec "{GATE}" "$@"\n', encoding="utf-8")
    hook.chmod(0o755)
    return project


def _catch_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.mark.integration
@pytest.mark.xfail(
    reason="the gate is still in KNOWN_UNCAUGHT — S3/S4 wiring removes this marker",
    strict=True,
)
def test_a_blocked_commit_records_a_catch(repo):
    """Fail-under-revert: drop the journal_catch from the gate and no row appears."""
    (repo / "leak.env").write_text('AWS_SECRET_ACCESS_KEY="AKIAIOSFODNN7EXAMPLE"\n')
    _git(repo, "add", "leak.env")

    proc = subprocess.run(  # noqa: S603 - git from PATH, as a developer runs it
        ["git", "commit", "-m", "wip"],  # noqa: S607
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode != 0, f"the privacy gate must block this commit:\n{proc.stderr}"
    # No session tree here (a bare shell commit), so the row is unattributed by
    # design (HATS-1634): a git gate's $0 carries no session id, and inventing one
    # would file the refusal under a session that never ran.
    rows = _catch_rows(repo / ".git/ai-hats/catches.jsonl")
    assert rows, f"the refusal left no catch row; stderr was:\n{proc.stderr}"
    row = rows[-1]
    assert row["kind"] == "catch"
    assert row["hook"] == "pre-commit-privacy.sh"
    assert row["verdict"] == "block"
    assert row["rule"] == "privacy"
    assert row["session_id"] == "", "no session tree and no env — must not claim one"
    assert "unattributed" in proc.stderr, "an unattributed record must say so (silent-fallback)"


@pytest.mark.integration
def test_a_clean_commit_records_nothing(repo):
    """Negative control: the gate that passes must stay out of the journal."""
    (repo / "fine.txt").write_text("nothing secret here\n")
    _git(repo, "add", "fine.txt")

    proc = subprocess.run(  # noqa: S603 - git from PATH
        ["git", "commit", "-m", "clean"],  # noqa: S607
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, f"a clean commit must pass:\n{proc.stderr}"
    assert not _catch_rows(repo / ".git/ai-hats/catches.jsonl")
