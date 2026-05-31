"""HATS-617 — end-to-end behaviour of the skill-lint pre-commit hook.

`pre-commit-skill-lint.sh` is a pure-bash surface; the unit suite cannot
meaningfully exercise it. This file drives the script against a real ephemeral
git repo, stubbing agnix through ``AI_HATS_SKILL_LINT_CMD`` so the test is
offline and deterministic (it verifies the HOOK's wiring — changed-files scope,
golang exclusion, fail-open, override, block-on-error — not agnix itself, which
is third-party and upstream-tested).

Covers:
  * blocks the commit when a staged library SKILL.md fails agnix
  * allows the commit when the staged skill passes
  * golang-* skills are excluded from the gate
  * non-skill / non-library changes are a no-op
  * fail-open when the agnix binary is absent
  * AI_HATS_SKILL_LINT_ACK=1 overrides the block

Slow only because of git init + subprocess spin-up (~ms each).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK = (
    REPO_ROOT
    / "library/usage/skills/skill-lint-gate/git_hooks/pre-commit-skill-lint.sh"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _make_stub(path: Path, rc: int, message: str = "") -> Path:
    """A fake agnix: prints `message` then exits with `rc`."""
    path.write_text(
        "#!/usr/bin/env bash\n"
        f"echo {message!r}\n"
        f"exit {rc}\n"
    )
    path.chmod(0o755)
    return path


def _run_hook(cwd: Path, env: dict | None = None, timeout: int = 10):
    base_env = os.environ.copy()
    base_env.pop("AI_HATS_SKILL_LINT_ACK", None)
    base_env.pop("AI_HATS_SKILL_LINT_CMD", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(HOOK)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=base_env,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An initialised git repo with a library/ tree and one committed skill."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    _git(tmp_path, "config", "user.email", "t@e.x")
    _git(tmp_path, "config", "user.name", "t")
    skill = tmp_path / "library/core/skills/existing/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: existing\ndescription: x\n---\n# Existing\n")
    _git(tmp_path, "add", "library/")
    _git(tmp_path, "commit", "-m", "init", "--quiet")
    return tmp_path


def _stage_skill(repo: Path, relpath: str, body: str = "# new\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    _git(repo, "add", relpath)


# --- scenarios -------------------------------------------------------------


@pytest.mark.integration
def test_blocks_when_staged_skill_fails(repo: Path, tmp_path: Path):
    """A staged authored SKILL.md that agnix rejects blocks the commit."""
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="AS-001 missing frontmatter")
    _stage_skill(repo, "library/core/skills/broken/SKILL.md")
    res = _run_hook(repo, env={"AI_HATS_SKILL_LINT_CMD": f"bash {stub}"})
    assert res.returncode == 1, res.stderr
    assert "[skill-lint] BLOCKED" in res.stderr
    assert "AS-001" in res.stderr


@pytest.mark.integration
def test_allows_when_staged_skill_passes(repo: Path, tmp_path: Path):
    """A staged skill that agnix accepts is allowed through."""
    stub = _make_stub(tmp_path / "pass.sh", rc=0)
    _stage_skill(repo, "library/core/skills/clean/SKILL.md")
    res = _run_hook(repo, env={"AI_HATS_SKILL_LINT_CMD": f"bash {stub}"})
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_golang_pack_is_excluded(repo: Path, tmp_path: Path):
    """golang-* skills are outside the gate even if agnix would fail them."""
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="would-fail")
    _stage_skill(repo, "library/usage/skills/golang-foo/SKILL.md")
    res = _run_hook(repo, env={"AI_HATS_SKILL_LINT_CMD": f"bash {stub}"})
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_non_library_change_is_noop(repo: Path, tmp_path: Path):
    """Staging a file outside library/**/SKILL.md does not trigger the gate."""
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="would-fail")
    (repo / "README.md").write_text("# Repo\n")
    _git(repo, "add", "README.md")
    res = _run_hook(repo, env={"AI_HATS_SKILL_LINT_CMD": f"bash {stub}"})
    assert res.returncode == 0, res.stderr


@pytest.mark.integration
def test_fail_open_when_agnix_absent(repo: Path):
    """Missing agnix binary → loud no-op (never wedge a commit)."""
    _stage_skill(repo, "library/core/skills/broken/SKILL.md")
    res = _run_hook(
        repo, env={"AI_HATS_SKILL_LINT_CMD": "/nonexistent/agnix-xyz"}
    )
    assert res.returncode == 0, res.stderr
    assert "SKIPPED" in res.stderr


@pytest.mark.integration
def test_ack_override_allows_block(repo: Path, tmp_path: Path):
    """AI_HATS_SKILL_LINT_ACK=1 bypasses the gate even on a failing skill."""
    stub = _make_stub(tmp_path / "fail.sh", rc=1, message="would-fail")
    _stage_skill(repo, "library/core/skills/broken/SKILL.md")
    res = _run_hook(
        repo,
        env={
            "AI_HATS_SKILL_LINT_CMD": f"bash {stub}",
            "AI_HATS_SKILL_LINT_ACK": "1",
        },
    )
    assert res.returncode == 0, res.stderr
    assert "AI_HATS_SKILL_LINT_ACK=1" in res.stderr
